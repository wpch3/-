package moze_intel.projecte.world_transmutation;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.mojang.serialization.DataResult;
import it.unimi.dsi.fastutil.objects.Reference2ObjectLinkedOpenHashMap;
import it.unimi.dsi.fastutil.objects.Reference2ObjectMap;
import it.unimi.dsi.fastutil.objects.Reference2ObjectMaps;
import it.unimi.dsi.fastutil.objects.Reference2ObjectOpenHashMap;
import java.util.Collections;
import java.util.Iterator;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Map.Entry;
import java.util.Objects;
import java.util.Optional;
import java.util.SequencedSet;
import java.util.function.Function;
import moze_intel.projecte.PECore;
import moze_intel.projecte.api.world_transmutation.IWorldTransmutation;
import moze_intel.projecte.api.world_transmutation.IWorldTransmutationFunction;
import moze_intel.projecte.api.world_transmutation.SimpleWorldTransmutation;
import moze_intel.projecte.api.world_transmutation.WorldTransmutation;
import moze_intel.projecte.api.world_transmutation.WorldTransmutationFile;
import moze_intel.projecte.network.packets.to_client.SyncWorldTransmutations;
import net.minecraft.resources.RegistryOps;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.packs.resources.ResourceManager;
import net.minecraft.server.packs.resources.SimpleJsonResourceReloadListener;
import net.minecraft.util.profiling.ProfilerFiller;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.state.BlockState;
import net.neoforged.neoforge.common.conditions.WithConditions;
import org.jetbrains.annotations.ApiStatus;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

public class WorldTransmutationManager extends SimpleJsonResourceReloadListener {

	//Copy of gson settings from RecipeManager's gson instance
	private static final Gson GSON = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();
	public static final WorldTransmutationManager INSTANCE = new WorldTransmutationManager();
	//Note: Assume we will only have one element for it, but allow it to grow if need be
	private static final Function<Block, SequencedSet<IWorldTransmutation>> SET_BUILDER = origin -> new LinkedHashSet<>(1);

	private Reference2ObjectMap<Block, SequencedSet<IWorldTransmutation>> entries = Reference2ObjectMaps.emptyMap();
	@Nullable
	private Reference2ObjectMap<Block, SequencedSet<IWorldTransmutation>> modifiedEntries = null;

	private WorldTransmutationManager() {
		super(GSON, "pe_world_transmutations");
	}

	public static SyncWorldTransmutations getSyncPacket() {
		return new SyncWorldTransmutations(INSTANCE.getWorldTransmutations());
	}

	@ApiStatus.Internal
	public void setEntries(Reference2ObjectMap<Block, SequencedSet<IWorldTransmutation>> transmutations) {
		this.entries = transmutations;
		this.modifiedEntries = null;
	}

	@Override
	protected void apply(@NotNull Map<ResourceLocation, JsonElement> object, @NotNull ResourceManager resourceManager, @NotNull ProfilerFiller profiler) {
		//Ensure we are interacting with the condition context
		RegistryOps<JsonElement> registryOps = makeConditionalOps();
		Reference2ObjectMap<Block, SequencedSet<IWorldTransmutation>> builder = new Reference2ObjectLinkedOpenHashMap<>();

		// Find all data/<domain>/pe_world_transmutations/foo/bar.json
		for (Entry<ResourceLocation, JsonElement> entry : object.entrySet()) {
			ResourceLocation file = entry.getKey();//<domain>:foo/bar
			DataResult<Optional<WithConditions<WorldTransmutationFile>>> result = WorldTransmutationFile.CONDITIONAL_CODEC.parse(registryOps, entry.getValue());
			if (result.isSuccess()) {
				Optional<WithConditions<WorldTransmutationFile>> decoded = result.getOrThrow();
				if (decoded.isPresent()) {
					for (IWorldTransmutation transmutation : decoded.get().carrier().transmutations()) {
						SequencedSet<IWorldTransmutation> transmutations = builder.computeIfAbsent(transmutation.origin().value(), SET_BUILDER);
						if (transmutations.add(transmutation)) {
							PECore.debugLog("World Transmutation File: '{}' registered {}", file, transmutation);
						} else {
							PECore.debugLog("World Transmutation File: '{}' registered {}. Skipped as it was identical to an already registered transmutation",
									file, transmutation);
						}
					}
				} else {
					PECore.debugLog("Skipping loading world transmutation file {} as its conditions were not met", file);
				}
			} else {
				result.ifError(error -> PECore.LOGGER.error("Parsing error loading world transmutation file {}: {}", file, error.message()));
			}
		}
		for (Iterator<Reference2ObjectMap.Entry<Block, SequencedSet<IWorldTransmutation>>> iterator = Reference2ObjectMaps.fastIterator(builder); iterator.hasNext(); ) {
			Reference2ObjectMap.Entry<Block, SequencedSet<IWorldTransmutation>> entry = iterator.next();
			int elements = entry.getValue().size();
			if (elements == 0) {//Note: It should never be empty, but validate it just in case
				iterator.remove();
			} else if (elements > 1) {//Multiple elements, so may not already be in the proper order
				SequencedSet<IWorldTransmutation> setBuilder = new LinkedHashSet<>(elements);
				//TODO: Figure out how do we want to resolve conflicts when the input is exactly the same, be it states or blocks
				boolean hasSimple = false;
				boolean hasComplex = false;
				for (IWorldTransmutation transmutation : entry.getValue()) {
					if (transmutation instanceof WorldTransmutation) {
						hasComplex = true;
						setBuilder.add(transmutation);
					} else {
						hasSimple = true;
					}
				}
				//Add any simple transmutations after the exact ones
				//Note: We only need to update the value if we have transmutations of different types
				// otherwise the read order is the best one to use
				if (hasSimple && hasComplex) {
					for (IWorldTransmutation transmutation : entry.getValue()) {
						if (transmutation instanceof SimpleWorldTransmutation) {
							setBuilder.add(transmutation);
						}
					}
					entry.setValue(setBuilder);
				}
			}
		}
		setEntries(builder);
	}

	/**
	 * @apiNote Do not modify this map.
	 */
	public Reference2ObjectMap<Block, SequencedSet<IWorldTransmutation>> getWorldTransmutations() {
		return modifiedEntries == null ? entries : modifiedEntries;
	}

	@Nullable
	public IWorldTransmutationFunction getWorldTransmutation(BlockState current) {
		return getWorldTransmutation(current, false);
	}

	@Nullable
	public IWorldTransmutationFunction getWorldTransmutation(BlockState current, boolean findAny) {
		if (current.isAir()) {
			return null;
		}
		SequencedSet<IWorldTransmutation> transmutations = getWorldTransmutations().getOrDefault(current.getBlock(), Collections.emptySortedSet());
		boolean hasComplex = false;
		for (IWorldTransmutation entry : transmutations) {
			if (entry.canTransmute(current)) {
				if (findAny) {
					//Note: If we just want to validate to make sure some valid transmutation exists, we can short circuit checking more exact state versions
					return entry;
				}
				if (hasComplex && entry instanceof SimpleWorldTransmutation) {
					Map<BlockState, IWorldTransmutation> exactStates = new Reference2ObjectOpenHashMap<>();
					for (IWorldTransmutation transmutation : transmutations) {
						if (transmutation instanceof WorldTransmutation worldTransmutation) {
							//TODO: Figure out how do we want to resolve conflicts when the input is exactly the same, be it states or blocks
							exactStates.putIfAbsent(worldTransmutation.originState(), transmutation);
						} else {
							break;
						}
					}
					//Note: exactStates should never be empty here
					return (input, isSneaking) -> Objects.requireNonNullElse(exactStates.get(input), entry).result(input, isSneaking);
				}
				return entry;
			} else if (entry instanceof WorldTransmutation) {
				hasComplex = true;
			}
		}
		return null;
	}

	/// Methods that exist for CrT integration

	@ApiStatus.Internal
	public void clearTransmutations() {
		//Explicitly mark that the modified version is empty
		this.modifiedEntries = Reference2ObjectMaps.emptyMap();
	}

	@ApiStatus.Internal
	public void resetWorldTransmutations() {
		modifiedEntries = null;
	}

	@ApiStatus.Internal
	public void register(IWorldTransmutation transmutation) {
		if (modifiedEntries == null) {
			makeEntriesMutable();
		} else if (modifiedEntries == Reference2ObjectMaps.<Block, SequencedSet<IWorldTransmutation>>emptyMap()) {
			//If we have no entries and the set we used was immutable, we need to switch to using a mutable one
			modifiedEntries = new Reference2ObjectLinkedOpenHashMap<>();
		}
		//Try to add the transmutation
		modifiedEntries.computeIfAbsent(transmutation.origin().value(), origin -> new LinkedHashSet<>()).add(transmutation);
	}

	@ApiStatus.Internal
	public void removeWorldTransmutation(IWorldTransmutation transmutation) {
		Block origin = transmutation.origin().value();
		boolean remove = modifiedEntries != null;
		if (!remove) {
			SequencedSet<IWorldTransmutation> transmutations = entries.get(origin);
			if (transmutations != null && transmutations.contains(transmutation)) {
				//Only bother instantiating the modified one if we actually have the transmutation
				makeEntriesMutable();
				remove = true;
			}
		}
		if (remove) {
			SequencedSet<IWorldTransmutation> transmutations = modifiedEntries.get(origin);
			if (transmutations != null && transmutations.remove(transmutation) && transmutations.isEmpty()) {
				modifiedEntries.remove(origin);
				if (modifiedEntries.isEmpty()) {
					modifiedEntries = Reference2ObjectMaps.emptyMap();
				}
			}
		}
	}

	private void makeEntriesMutable() {
		modifiedEntries = new Reference2ObjectLinkedOpenHashMap<>(entries.size());
		for (Map.Entry<Block, SequencedSet<IWorldTransmutation>> entry : entries.entrySet()) {
			modifiedEntries.put(entry.getKey(), new LinkedHashSet<>(entry.getValue()));
		}
	}
}