package moze_intel.projecte.gameObjs.items.armor;

import moze_intel.projecte.gameObjs.registries.PEArmorMaterials;
import moze_intel.projecte.gameObjs.registries.PEItems;
import net.minecraft.tags.DamageTypeTags;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ArmorItem;

public abstract class GemArmorBase extends PEArmor {

	public GemArmorBase(ArmorItem.Type armorType, Properties props) {
		super(PEArmorMaterials.GEM_ARMOR, armorType, props);
	}

	@Override
	public float getFullSetBaseReduction() {
		return 0.9F;
	}

	@Override
	public float getMaxDamageAbsorb(ArmorItem.Type type, DamageSource source) {
		if (source.is(DamageTypeTags.IS_EXPLOSION)) {
			return 750;
		}
		if (type == ArmorItem.Type.BOOTS && source.is(DamageTypeTags.IS_FALL)) {
			return 15 / getPieceEffectiveness(type);
		} else if (type == ArmorItem.Type.HELMET && source.is(DamageTypeTags.IS_DROWNING)) {
			return 15 / getPieceEffectiveness(type);
		}
		if (source.is(DamageTypeTags.BYPASSES_ARMOR)) {
			return 0;
		}
		//If the source is not unblockable, allow our piece to block a certain amount of damage
		if (type == ArmorItem.Type.HELMET || type == ArmorItem.Type.BOOTS) {
			return 400;
		}
		return 500;
	}

	public static boolean hasAnyPiece(Player player) {
		return player.getItemBySlot(EquipmentSlot.HEAD).is(PEItems.GEM_HELMET) ||
			   player.getItemBySlot(EquipmentSlot.CHEST).is(PEItems.GEM_CHESTPLATE) ||
			   player.getItemBySlot(EquipmentSlot.LEGS).is(PEItems.GEM_LEGGINGS) ||
			   player.getItemBySlot(EquipmentSlot.FEET).is(PEItems.GEM_BOOTS);
	}

	public static boolean hasFullSet(Player player) {
		return player.getItemBySlot(EquipmentSlot.HEAD).is(PEItems.GEM_HELMET) &&
			   player.getItemBySlot(EquipmentSlot.CHEST).is(PEItems.GEM_CHESTPLATE) &&
			   player.getItemBySlot(EquipmentSlot.LEGS).is(PEItems.GEM_LEGGINGS) &&
			   player.getItemBySlot(EquipmentSlot.FEET).is(PEItems.GEM_BOOTS);
	}
}