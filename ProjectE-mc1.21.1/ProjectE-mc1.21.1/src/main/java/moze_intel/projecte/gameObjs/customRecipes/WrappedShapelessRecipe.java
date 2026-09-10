package moze_intel.projecte.gameObjs.customRecipes;

import net.minecraft.core.HolderLookup;
import net.minecraft.core.NonNullList;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.crafting.CraftingBookCategory;
import net.minecraft.world.item.crafting.CraftingInput;
import net.minecraft.world.item.crafting.CraftingRecipe;
import net.minecraft.world.item.crafting.Ingredient;
import net.minecraft.world.item.crafting.ShapelessRecipe;
import net.minecraft.world.level.Level;
import org.jetbrains.annotations.NotNull;

//From Mekanism's shaped recipe wrapper
public abstract class WrappedShapelessRecipe implements CraftingRecipe {

    private final ShapelessRecipe internal;

    protected WrappedShapelessRecipe(ShapelessRecipe internal) {
        this.internal = internal;
    }

    public ShapelessRecipe getInternal() {
        return internal;
    }

    @NotNull
    @Override
    public CraftingBookCategory category() {
        return internal.category();
    }

    @NotNull
    @Override
    public abstract ItemStack assemble(@NotNull CraftingInput inv, @NotNull HolderLookup.Provider registryAccess);

    @Override
    public boolean matches(@NotNull CraftingInput inv, @NotNull Level world) {
        //Note: We do not override the matches method if it matches ignoring data components,
        // to ensure that we return the proper value for if there is a match that gives a proper output
        return internal.matches(inv, world) && !assemble(inv, world.registryAccess()).isEmpty();
    }

    @Override
    public boolean canCraftInDimensions(int width, int height) {
        return internal.canCraftInDimensions(width, height);
    }

    @NotNull
    @Override
    public ItemStack getResultItem(@NotNull HolderLookup.Provider registryAccess) {
        return internal.getResultItem(registryAccess);
    }

    @NotNull
    @Override
    public NonNullList<ItemStack> getRemainingItems(@NotNull CraftingInput inv) {
        return internal.getRemainingItems(inv);
    }

    @NotNull
    @Override
    public NonNullList<Ingredient> getIngredients() {
        return internal.getIngredients();
    }

    @Override
    public boolean isSpecial() {
        return internal.isSpecial();
    }

    @NotNull
    @Override
    public String getGroup() {
        return internal.getGroup();
    }

    @NotNull
    @Override
    public ItemStack getToastSymbol() {
        return internal.getToastSymbol();
    }

    @Override
    public boolean isIncomplete() {
        return internal.isIncomplete();
    }
}