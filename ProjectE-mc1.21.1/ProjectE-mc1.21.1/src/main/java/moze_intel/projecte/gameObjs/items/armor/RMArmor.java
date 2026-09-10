package moze_intel.projecte.gameObjs.items.armor;

import moze_intel.projecte.gameObjs.registries.PEArmorMaterials;
import net.minecraft.tags.DamageTypeTags;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.item.ArmorItem;

public class RMArmor extends PEArmor {

	public RMArmor(ArmorItem.Type armorType, Properties props) {
		super(PEArmorMaterials.RED_MATTER, armorType, props);
	}

	@Override
	public float getFullSetBaseReduction() {
		return 0.9F;
	}

	@Override
	public float getMaxDamageAbsorb(ArmorItem.Type type, DamageSource source) {
		if (source.is(DamageTypeTags.IS_EXPLOSION)) {
			return 500;
		}
		if (type == ArmorItem.Type.BOOTS && source.is(DamageTypeTags.IS_FALL)) {
			return 10 / getPieceEffectiveness(type);
		} else if (type == ArmorItem.Type.HELMET && source.is(DamageTypeTags.IS_DROWNING)) {
			return 10 / getPieceEffectiveness(type);
		}
		if (source.is(DamageTypeTags.BYPASSES_ARMOR)) {
			return 0;
		}
		//If the source is not unblockable, allow our piece to block a certain amount of damage
		if (type == ArmorItem.Type.HELMET || type == ArmorItem.Type.BOOTS) {
			return 250;
		}
		return 350;
	}
}