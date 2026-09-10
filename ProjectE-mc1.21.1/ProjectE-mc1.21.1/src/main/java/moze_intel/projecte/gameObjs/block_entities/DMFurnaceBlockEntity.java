package moze_intel.projecte.gameObjs.block_entities;

import it.unimi.dsi.fastutil.objects.Object2IntMap;
import it.unimi.dsi.fastutil.objects.Object2IntMaps;
import it.unimi.dsi.fastutil.objects.Object2IntOpenHashMap;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;
import java.util.Optional;
import moze_intel.projecte.api.capabilities.PECapabilities;
import moze_intel.projecte.api.capabilities.item.IItemEmcHolder;
import moze_intel.projecte.gameObjs.blocks.MatterFurnace;
import moze_intel.projecte.gameObjs.container.DMFurnaceContainer;
import moze_intel.projecte.gameObjs.container.slots.SlotPredicates;
import moze_intel.projecte.gameObjs.registration.impl.BlockEntityTypeRegistryObject;
import moze_intel.projecte.gameObjs.registries.PEBlockEntityTypes;
import moze_intel.projecte.utils.WorldHelper;
import moze_intel.projecte.utils.text.PELang;
import net.minecraft.SharedConstants;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.HolderLookup;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.util.Mth;
import net.minecraft.util.RandomSource;
import net.minecraft.world.MenuProvider;
import net.minecraft.world.entity.ExperienceOrb;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.RecipeCraftingHolder;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.item.crafting.RecipeHolder;
import net.minecraft.world.item.crafting.RecipeManager;
import net.minecraft.world.item.crafting.RecipeType;
import net.minecraft.world.item.crafting.SingleRecipeInput;
import net.minecraft.world.item.crafting.SmeltingRecipe;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity;
import net.minecraft.world.level.block.entity.Hopper;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.Vec3;
import net.neoforged.neoforge.capabilities.BlockCapabilityCache;
import net.neoforged.neoforge.capabilities.Capabilities.ItemHandler;
import net.neoforged.neoforge.capabilities.ICapabilityProvider;
import net.neoforged.neoforge.common.Tags;
import net.neoforged.neoforge.items.IItemHandler;
import net.neoforged.neoforge.items.IItemHandlerModifiable;
import net.neoforged.neoforge.items.ItemHandlerHelper;
import net.neoforged.neoforge.items.wrapper.CombinedInvWrapper;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;
import org.jetbrains.annotations.Range;

public class DMFurnaceBlockEntity extends EmcBlockEntity implements MenuProvider, RecipeCraftingHolder {

	public static final ICapabilityProvider<DMFurnaceBlockEntity, @Nullable Direction, IItemHandler> INVENTORY_PROVIDER = (furnace, side) -> {
		if (side == null) {
			return furnace.joined;
		} else if (side == Direction.UP) {
			return furnace.automationInput;
		} else if (side == Direction.DOWN) {
			return furnace.automationOutput;
		}
		return furnace.automationSides;
	};
	private static final long EMC_CONSUMPTION = 2;

	private final CompactableStackHandler inputInventory = new CompactableStackHandler(getInvSize()) {
		private ItemStack oldInput = ItemStack.EMPTY;

		@Override
		protected void onLoad() {
			oldInput = getStackInSlot(0).copy();
		}

		@Override
		protected void onContentsChanged(int slot) {
			super.onContentsChanged(slot);
			if (slot == 0) {
				ItemStack input = getStackInSlot(0);
				if (!ItemStack.isSameItemSameComponents(oldInput, input)) {
					//Reset the cooking progress
					RecipeResult recipeResult = getSmeltingRecipe(input);
					cookingTotalTime = getTotalCookTime(recipeResult);
					cookingProgress = 0;
					oldInput = input.copy();
				}
			}
		}
	};
	private final CompactableStackHandler outputInventory = new CompactableStackHandler(getInvSize());
	private final StackHandler fuelInv = new StackHandler(1);

	private final IItemHandler joined;
	private final IItemHandlerModifiable automationInput;
	private final IItemHandlerModifiable automationOutput;
	private final IItemHandler automationSides;

	protected final int ticksBeforeSmelt;
	private final int efficiencyBonus;
	private final Object2IntOpenHashMap<ResourceLocation> recipesUsed = new Object2IntOpenHashMap<>();
	private final RecipeManager.CachedCheck<SingleRecipeInput, SmeltingRecipe> quickCheck;

	@Nullable
	private BlockCapabilityCache<IItemHandler, @Nullable Direction> pullTarget;
	@Nullable
	private BlockCapabilityCache<IItemHandler, @Nullable Direction> pushTarget;

	public int litTime;
	public int litDuration;
	public int cookingProgress;
	public int cookingTotalTime;

	public DMFurnaceBlockEntity(BlockPos pos, BlockState state) {
		this(PEBlockEntityTypes.DARK_MATTER_FURNACE, pos, state, SharedConstants.TICKS_PER_SECOND / 2, 3);
	}

	protected DMFurnaceBlockEntity(BlockEntityTypeRegistryObject<? extends DMFurnaceBlockEntity> type, BlockPos pos, BlockState state, int ticksBeforeSmelt, int efficiencyBonus) {
		super(type, pos, state, 64);
		this.ticksBeforeSmelt = ticksBeforeSmelt;
		this.efficiencyBonus = efficiencyBonus;
		this.quickCheck = RecipeManager.createCheck(RecipeType.SMELTING);

		this.automationInput = new WrappedItemHandler(inputInventory, WrappedItemHandler.WriteMode.IN) {
			@NotNull
			@Override
			public ItemStack insertItem(int slot, @NotNull ItemStack stack, boolean simulate) {
				return hasSmeltingResult(stack) ? super.insertItem(slot, stack, simulate) : stack;
			}
		};
		this.automationOutput = new WrappedItemHandler(outputInventory, WrappedItemHandler.WriteMode.OUT);
		IItemHandlerModifiable automationFuel = new WrappedItemHandler(fuelInv, WrappedItemHandler.WriteMode.IN) {
			@NotNull
			@Override
			public ItemStack insertItem(int slot, @NotNull ItemStack stack, boolean simulate) {
				return SlotPredicates.FURNACE_FUEL.test(stack) ? super.insertItem(slot, stack, simulate) : stack;
			}
		};
		this.automationSides = new CombinedInvWrapper(automationFuel, automationOutput);
		this.joined = new CombinedInvWrapper(automationInput, automationFuel, automationOutput);
	}

	@Override
	public void setLevel(@NotNull Level level) {
		super.setLevel(level);
		if (level instanceof ServerLevel serverLevel) {
			pullTarget = BlockCapabilityCache.create(ItemHandler.BLOCK, serverLevel, worldPosition.above(), Direction.DOWN);
			pushTarget = BlockCapabilityCache.create(ItemHandler.BLOCK, serverLevel, worldPosition.below(), Direction.UP);
		}
	}

	@Override
	protected boolean canProvideEmc() {
		return false;
	}

	@Override
	@Range(from = 0, to = Long.MAX_VALUE)
	protected long getEmcInsertLimit() {
		return EMC_CONSUMPTION;
	}

	protected int getInvSize() {
		return 9;
	}

	protected float getOreDoubleChance() {
		return 0.5F;
	}

	protected float getDoubleChance(ItemStack input) {
		if (input.is(Tags.Items.ORES)) {
			return getOreDoubleChance();
		} else if (input.is(Tags.Items.RAW_MATERIALS)) {
			//Base rate for raw ore doubling chance is: 1 -> 1.333 which means we multiply our ore double chance by 2/3
			return getOreDoubleChance() * 2 / 3;
		}
		return 0;
	}

	public float getBurnProgress() {
		if (cookingTotalTime == 0) {
			return 0;
		}
		//Adjust by one so that it can look like it is actually reaching the end of the bar
		int progress = isLit() && canSmelt(getSmeltingRecipe(getItemToSmelt())) ? cookingProgress + 1 : cookingProgress;
		return Mth.clamp(progress / (float) cookingTotalTime, 0, 1);
	}

	@NotNull
	@Override
	public AbstractContainerMenu createMenu(int windowId, @NotNull Inventory playerInv, @NotNull Player playerIn) {
		return new DMFurnaceContainer(windowId, playerInv, this);
	}

	@NotNull
	@Override
	public Component getDisplayName() {
		return PELang.GUI_DARK_MATTER_FURNACE.translate();
	}

	public IItemHandler getFuel() {
		return fuelInv;
	}

	private ItemStack getItemToSmelt() {
		return inputInventory.getStackInSlot(0);
	}

	private ItemStack getFuelItem() {
		return fuelInv.getStackInSlot(0);
	}

	public IItemHandler getInput() {
		return inputInventory;
	}

	public IItemHandler getOutput() {
		return outputInventory;
	}

	public static void tickServer(Level level, BlockPos pos, BlockState state, DMFurnaceBlockEntity furnace) {
		boolean wasBurning = furnace.isLit();
		int lastLitTime = furnace.litTime;
		int lastCookingProgress = furnace.cookingProgress;
		if (furnace.isLit()) {
			--furnace.litTime;
		}
		furnace.inputInventory.compact();
		furnace.outputInventory.compact();
		furnace.pullFromInventories(level, pos);

		RecipeResult recipeResult = furnace.getSmeltingRecipe(level, furnace.getItemToSmelt());
		boolean canSmelt = furnace.canSmelt(recipeResult);
		ItemStack fuelItem = furnace.getFuelItem();
		if (canSmelt) {
			IItemEmcHolder emcHolder = fuelItem.getCapability(PECapabilities.EMC_HOLDER_ITEM_CAPABILITY);
			if (emcHolder != null) {
				long simulatedExtraction = emcHolder.extractEmc(fuelItem, EMC_CONSUMPTION, EmcAction.SIMULATE);
				if (simulatedExtraction == EMC_CONSUMPTION) {
					furnace.forceInsertEmc(emcHolder.extractEmc(fuelItem, simulatedExtraction, EmcAction.EXECUTE), EmcAction.EXECUTE);
				}
				furnace.markDirty(level, pos, false);
			}
		}

		if (furnace.getStoredEmc() >= EMC_CONSUMPTION) {
			furnace.litTime = 1;
			furnace.forceExtractEmc(EMC_CONSUMPTION, EmcAction.EXECUTE);
		}

		if (canSmelt) {
			if (furnace.litTime == 0) {
				furnace.litDuration = furnace.litTime = furnace.getItemBurnTime(fuelItem);
				if (furnace.isLit() && !fuelItem.isEmpty()) {
					ItemStack copy = fuelItem.copy();
					fuelItem.shrink(1);
					furnace.fuelInv.onContentsChanged(0);
					if (fuelItem.isEmpty()) {
						furnace.fuelInv.setStackInSlot(0, copy.getItem().getCraftingRemainingItem(copy));
					}
					furnace.markDirty(level, pos, false);
				}
			}
			if (furnace.isLit() && ++furnace.cookingProgress == furnace.cookingTotalTime) {
				furnace.cookingProgress = 0;
				furnace.cookingTotalTime = furnace.getTotalCookTime(recipeResult);
				furnace.smeltItem(level, recipeResult);
			}
		} else {
			furnace.cookingProgress = 0;
		}
		if (wasBurning != furnace.isLit()) {
			if (state.getBlock() instanceof MatterFurnace) {
				//Should always be true, but validate it just in case
				level.setBlockAndUpdate(pos, state.setValue(MatterFurnace.LIT, furnace.isLit()));
			}
			furnace.markDirty(level, pos, true);
		}
		furnace.pushToInventories(level, pos);
		if (lastLitTime != furnace.litTime || lastCookingProgress != furnace.cookingProgress) {
			furnace.markDirty(level, pos, false);
		}
		furnace.updateComparators(level, pos);
	}

	public boolean isLit() {
		return litTime > 0;
	}

	private static boolean isHopper(@NotNull Level level, @NotNull BlockPos position) {
		//We let hoppers go at their normal rate
		return WorldHelper.getBlockEntity(level, position) instanceof Hopper;
	}

	private void pullFromInventories(@NotNull Level level, @NotNull BlockPos pos) {
		if (pullTarget == null || isHopper(level, pos.above())) {
			return;
		}
		IItemHandler handler = pullTarget.getCapability();
		if (handler != null) {
			for (int i = 0, slots = handler.getSlots(); i < slots; i++) {
				ItemStack extractTest = handler.extractItem(i, Integer.MAX_VALUE, true);
				if (!extractTest.isEmpty()) {
					IItemHandler targetInv = SlotPredicates.FURNACE_FUEL.test(extractTest) ? fuelInv : inputInventory;
					transferItem(targetInv, i, extractTest, handler);
				}
			}
		}
	}

	private void pushToInventories(@NotNull Level level, @NotNull BlockPos pos) {
		if (pushTarget == null || outputInventory.isEmpty() || isHopper(level, pos.below())) {
			return;
		}
		IItemHandler targetInv = pushTarget.getCapability();
		if (targetInv != null) {
			for (int i = 0, slots = outputInventory.getSlots(); i < slots; i++) {
				ItemStack extractTest = outputInventory.extractItem(i, Integer.MAX_VALUE, true);
				if (!extractTest.isEmpty()) {
					transferItem(targetInv, i, extractTest, outputInventory);
				}
			}
		}
	}

	private void transferItem(IItemHandler targetInv, int i, ItemStack extractTest, IItemHandler outputInventory) {
		ItemStack remainderTest = ItemHandlerHelper.insertItemStacked(targetInv, extractTest, true);
		int successfullyTransferred = extractTest.getCount() - remainderTest.getCount();
		if (successfullyTransferred > 0) {
			ItemStack toInsert = outputInventory.extractItem(i, successfullyTransferred, false);
			ItemStack result = ItemHandlerHelper.insertItemStacked(targetInv, toInsert, false);
			assert result.isEmpty();
		}
	}

	private RecipeResult getSmeltingRecipe(ItemStack input) {
		return getSmeltingRecipe(level, input);
	}

	private RecipeResult getSmeltingRecipe(@Nullable Level level, ItemStack input) {
		if (level == null || input.isEmpty()) {
			return RecipeResult.EMPTY;
		}
		//Note: We copy the input and fuel so that if anyone attempts to mutate the input from assemble then there is no side effects that occur
		SingleRecipeInput recipeInput = new SingleRecipeInput(input.copyWithCount(1));
		Optional<RecipeHolder<SmeltingRecipe>> optionalRecipe = quickCheck.getRecipeFor(recipeInput, level);
		if (optionalRecipe.isPresent()) {
			RecipeHolder<SmeltingRecipe> recipeHolder = optionalRecipe.get();
			return new RecipeResult(recipeHolder, recipeHolder.value().assemble(recipeInput, level.registryAccess()));
		}
		return RecipeResult.EMPTY;
	}

	public boolean hasSmeltingResult(ItemStack input) {
		return getSmeltingRecipe(input).hasResult();
	}

	private void smeltItem(@NotNull Level level, @NotNull RecipeResult recipeResult) {
		ItemStack toSmelt = getItemToSmelt();
		ItemStack smeltResult = recipeResult.scaledResult(level.random, getDoubleChance(toSmelt));
		if (!smeltResult.isEmpty()) {//Double-check the result isn't somehow empty
			ItemHandlerHelper.insertItemStacked(outputInventory, smeltResult, false);

			if (toSmelt.is(Items.WET_SPONGE)) {
				//Hardcoded handling of wet sponge to filling a bucket with water
				ItemStack fuelItem = getFuelItem();
				if (!fuelItem.isEmpty() && fuelItem.is(Items.BUCKET)) {
					fuelInv.setStackInSlot(0, new ItemStack(Items.WATER_BUCKET));
				}
			}

			toSmelt.shrink(1);
			inputInventory.onContentsChanged(0);
			setRecipeUsed(recipeResult.recipeHolder());
		}
	}

	private boolean canSmelt(RecipeResult recipeResult) {
		ItemStack smeltResult = recipeResult.result();
		if (smeltResult.isEmpty()) {
			return false;
		}
		ItemStack currentSmelted = outputInventory.getStackInSlot(outputInventory.getSlots() - 1);
		if (currentSmelted.isEmpty()) {
			return true;
		} else if (!ItemStack.isSameItemSameComponents(smeltResult, currentSmelted)) {
			return false;
		}
		int result = currentSmelted.getCount() + smeltResult.getCount();
		return result <= currentSmelted.getMaxStackSize();
	}

	private int getItemBurnTime(ItemStack stack) {
		return stack.getBurnTime(RecipeType.SMELTING) * ticksBeforeSmelt / AbstractFurnaceBlockEntity.BURN_TIME_STANDARD * efficiencyBonus;
	}

	private int getTotalCookTime(RecipeResult recipeResult) {
		if (recipeResult.recipeHolder() == null) {
			return ticksBeforeSmelt;
		}
		int cookingTime = recipeResult.recipeHolder().value().getCookingTime();
		return Mth.ceil(ticksBeforeSmelt * cookingTime / (float) AbstractFurnaceBlockEntity.BURN_TIME_STANDARD);
	}

	public float getLitProgress() {
		int litDuration = this.litDuration;
		if (litDuration == 0) {
			litDuration = ticksBeforeSmelt;
		}
		return Mth.clamp(litTime / (float) litDuration, 0, 1);
	}

	@Override
	public void loadAdditional(@NotNull CompoundTag tag, @NotNull HolderLookup.Provider registries) {
		super.loadAdditional(tag, registries);
		litTime = tag.getInt("burn_time");
		cookingProgress = tag.getInt("cook_time");
		cookingTotalTime = tag.getInt("cook_time_total");
		fuelInv.deserializeNBT(registries, tag.getCompound("fuel"));
		inputInventory.deserializeNBT(registries, tag.getCompound("input"));
		outputInventory.deserializeNBT(registries, tag.getCompound("output"));
		litDuration = getItemBurnTime(getFuelItem());
		//[VanillaCopy] AbstractFurnaceBlockEntity
		CompoundTag usedRecipes = tag.getCompound("recipes_used");
		for (String recipeId : usedRecipes.getAllKeys()) {
			this.recipesUsed.put(ResourceLocation.parse(recipeId), usedRecipes.getInt(recipeId));
		}
	}

	@Override
	protected void saveAdditional(@NotNull CompoundTag tag, @NotNull HolderLookup.Provider registries) {
		super.saveAdditional(tag, registries);
		tag.putInt("burn_time", litTime);
		tag.putInt("cook_time", cookingProgress);
		tag.putInt("cook_time_total", this.cookingTotalTime);
		tag.put("input", inputInventory.serializeNBT(registries));
		tag.put("output", outputInventory.serializeNBT(registries));
		tag.put("fuel", fuelInv.serializeNBT(registries));
		//[VanillaCopy] AbstractFurnaceBlockEntity
		CompoundTag usedRecipes = new CompoundTag();
		for (Iterator<Object2IntMap.Entry<ResourceLocation>> iterator = Object2IntMaps.fastIterator(recipesUsed); iterator.hasNext(); ) {
			Object2IntMap.Entry<ResourceLocation> entry = iterator.next();
			usedRecipes.putInt(entry.getKey().toString(), entry.getIntValue());
		}
		tag.put("recipes_used", usedRecipes);
	}

	@Override
	public void setRecipeUsed(@Nullable RecipeHolder<?> recipeHolder) {
		//[VanillaCopy] AbstractFurnaceBlockEntity
		if (recipeHolder != null) {
			this.recipesUsed.addTo(recipeHolder.id(), 1);
		}
	}

	@Nullable
	@Override
	public RecipeHolder<?> getRecipeUsed() {
		//[VanillaCopy] AbstractFurnaceBlockEntity, always return null
		return null;
	}

	@Override
	public void awardUsedRecipes(@NotNull Player player, @NotNull List<ItemStack> items) {
		//[VanillaCopy] AbstractFurnaceBlockEntity, no-op
	}

	//[VanillaCopy] AbstractFurnaceBlockEntity
	public void awardUsedRecipesAndPopExperience(ServerPlayer player) {
		List<RecipeHolder<?>> recipes = getRecipesToAwardAndPopExperience(player.serverLevel(), player.position());
		player.awardRecipes(recipes);

		for (RecipeHolder<?> recipeholder : recipes) {
			//Note: We don't have a good way to access the list of input items that were present, so we just skip it
			// and only support triggering recipe triggers that are based on the recipe id
			player.triggerRecipeCrafted(recipeholder, Collections.emptyList());
		}

		this.recipesUsed.clear();
	}

	//[VanillaCopy] AbstractFurnaceBlockEntity
	public List<RecipeHolder<?>> getRecipesToAwardAndPopExperience(ServerLevel level, Vec3 popVec) {
		RecipeManager recipeManager = level.getRecipeManager();
		List<RecipeHolder<?>> list = new ArrayList<>();
		for (Iterator<Object2IntMap.Entry<ResourceLocation>> iterator = Object2IntMaps.fastIterator(recipesUsed); iterator.hasNext(); ) {
			Object2IntMap.Entry<ResourceLocation> entry = iterator.next();
			Optional<RecipeHolder<?>> optionalRecipe = recipeManager.byKey(entry.getKey());
			if (optionalRecipe.isPresent()) {
				RecipeHolder<?> recipeHolder = optionalRecipe.get();
				list.add(recipeHolder);
				//Validate it is actually a cooking recipe
				if (recipeHolder.value() instanceof SmeltingRecipe recipe) {
					createExperience(level, popVec, entry.getIntValue(), recipe.getExperience());
				}
			}
		}
		return list;
	}

	//[VanillaCopy] AbstractFurnaceBlockEntity
	private static void createExperience(ServerLevel level, Vec3 popVec, int recipeIndex, float experience) {
		float indexBasedExperience = recipeIndex * experience;
		int amount = Mth.floor(indexBasedExperience);
		float partial = indexBasedExperience - amount;
		if (partial != 0.0F && Math.random() < (double) partial) {
			++amount;
		}

		ExperienceOrb.award(level, popVec, amount);
	}

	private record RecipeResult(@Nullable RecipeHolder<SmeltingRecipe> recipeHolder, ItemStack result) {

		private static final RecipeResult EMPTY = new RecipeResult(null, ItemStack.EMPTY);

		public ItemStack scaledResult(RandomSource random, float doubleChance) {
			if (random.nextFloat() < doubleChance) {
				return result.copyWithCount(2 * result.getCount());
			}
			return result.copy();
		}

		public boolean hasResult() {
			return !result.isEmpty();
		}
	}
}