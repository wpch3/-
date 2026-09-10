package moze_intel.projecte.client.integration.emi;

import com.mojang.serialization.Codec;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import moze_intel.projecte.integration.IntegrationHelper;
import net.minecraft.core.HolderLookup;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.data.CachedOutput;
import net.minecraft.data.DataProvider;
import net.minecraft.data.PackOutput;
import net.minecraft.data.PackOutput.PathProvider;
import net.minecraft.data.PackOutput.Target;
import net.minecraft.resources.ResourceKey;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.packs.PackType;
import net.minecraft.util.ExtraCodecs;
import net.minecraft.world.level.ItemLike;
import net.neoforged.neoforge.common.data.ExistingFileHelper;
import org.jetbrains.annotations.NotNull;

/**
 * From Mekanism
 */
public abstract class BaseEmiDefaults implements DataProvider {

	private static final Codec<List<ResourceLocation>> CODEC = ExtraCodecs.nonEmptyList(ResourceLocation.CODEC.listOf())
			.fieldOf("added")
			.codec();

	private final CompletableFuture<HolderLookup.Provider> registries;
	private final Set<ResourceLocation> recipes = new HashSet<>();
	private final ExistingFileHelper existingFileHelper;
	private final PathProvider pathProvider;
	private final String modid;

	protected BaseEmiDefaults(PackOutput output, ExistingFileHelper existingFileHelper, CompletableFuture<HolderLookup.Provider> registries, String modid) {
		this.pathProvider = output.createPathProvider(Target.RESOURCE_PACK, "recipe/defaults");
		this.existingFileHelper = existingFileHelper;
		this.registries = registries;
		this.modid = modid;
	}

	@NotNull
	@Override
	public String getName() {
		return "EMI Default Recipe Provider: " + modid;
	}

	@NotNull
	@Override
	public final CompletableFuture<?> run(@NotNull CachedOutput cachedOutput) {
		return this.registries.thenCompose(lookupProvider -> {
			addDefaults(lookupProvider);
			//Sort to make the output more stable
			List<ResourceLocation> sortedRecipes = new ArrayList<>(recipes);
			sortedRecipes.sort(ResourceLocation::compareNamespaced);
			Path path = pathProvider.json(ResourceLocation.fromNamespaceAndPath(IntegrationHelper.EMI_MODID, modid));
			return DataProvider.saveStable(cachedOutput, lookupProvider, CODEC, sortedRecipes, path);
		});
	}

	protected abstract void addDefaults(HolderLookup.Provider lookupProvider);

	protected void addRecipe(ItemLike output) {
		ResourceLocation registryName = BuiltInRegistries.ITEM.getResourceKey(output.asItem())
				.map(ResourceKey::location)
				.orElseThrow(() -> new IllegalStateException("Could not retrieve registry name for output."));
		addRecipe(registryName);
	}

	protected void addRecipe(String recipePath) {
		addRecipe(ResourceLocation.fromNamespaceAndPath(modid, recipePath));
	}

	protected void addRecipe(ResourceLocation recipe) {
		if (recipeExists(recipe)) {
			addUncheckedRecipe(recipe);
		} else {
			throw new IllegalArgumentException("Recipe '" + recipe + "' does not exist.");
		}
	}

	protected void addUncheckedRecipe(ResourceLocation recipe) {
		if (!recipes.add(recipe)) {
			throw new IllegalArgumentException("Recipe '" + recipe + "' was added multiple times.");
		}
	}

	public boolean recipeExists(ResourceLocation location) {
		return existingFileHelper.exists(location, PackType.SERVER_DATA, ".json", "recipes");
	}
}