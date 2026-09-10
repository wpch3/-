package moze_intel.projecte.gameObjs.customRecipes;

import com.mojang.serialization.MapCodec;
import java.util.function.Function;
import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.world.item.crafting.Recipe;
import net.minecraft.world.item.crafting.RecipeSerializer;
import net.minecraft.world.item.crafting.ShapelessRecipe;

public record PERecipeSerializer<RECIPE extends Recipe<?>>(MapCodec<RECIPE> codec, StreamCodec<RegistryFriendlyByteBuf, RECIPE> streamCodec) implements RecipeSerializer<RECIPE> {

	public static <RECIPE extends WrappedShapelessRecipe> PERecipeSerializer<RECIPE> wrapped(Function<ShapelessRecipe, RECIPE> wrapper) {
		return new PERecipeSerializer<>(
				RecipeSerializer.SHAPELESS_RECIPE.codec().xmap(wrapper, WrappedShapelessRecipe::getInternal),
				RecipeSerializer.SHAPELESS_RECIPE.streamCodec().map(wrapper, WrappedShapelessRecipe::getInternal)
		);
	}
}