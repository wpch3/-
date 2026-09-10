package moze_intel.projecte.common.tag;

import java.util.concurrent.CompletableFuture;
import moze_intel.projecte.PECore;
import moze_intel.projecte.gameObjs.registries.PEDamageTypes;
import net.minecraft.core.HolderLookup;
import net.minecraft.core.HolderLookup.Provider;
import net.minecraft.core.registries.Registries;
import net.minecraft.data.PackOutput;
import net.minecraft.data.tags.TagsProvider;
import net.minecraft.resources.ResourceKey;
import net.minecraft.tags.DamageTypeTags;
import net.minecraft.world.damagesource.DamageType;
import net.neoforged.neoforge.common.Tags;
import net.neoforged.neoforge.common.data.ExistingFileHelper;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

public class PEDamageTypeTagsProvider extends TagsProvider<DamageType> {

	public PEDamageTypeTagsProvider(PackOutput output, CompletableFuture<Provider> lookupProvider, @Nullable ExistingFileHelper existingFileHelper) {
		super(output, Registries.DAMAGE_TYPE, lookupProvider, PECore.MODID, existingFileHelper);
	}

	@Override
	protected void addTags(@NotNull HolderLookup.Provider provider) {
		ResourceKey<DamageType> playerAttack = PEDamageTypes.BYPASS_ARMOR_PLAYER_ATTACK.key();
		tag(DamageTypeTags.BYPASSES_ARMOR).add(playerAttack);
		tag(DamageTypeTags.CAN_BREAK_ARMOR_STAND).add(playerAttack);
		tag(DamageTypeTags.IS_PLAYER_ATTACK).add(playerAttack);
		tag(DamageTypeTags.PANIC_CAUSES).add(playerAttack);
		tag(Tags.DamageTypes.IS_PHYSICAL).add(playerAttack);
	}

	@NotNull
	@Override
	public String getName() {
		return "Damage Type Tags";
	}
}