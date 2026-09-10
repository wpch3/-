package moze_intel.projecte.gameObjs.items.armor;

import moze_intel.projecte.gameObjs.registries.PEArmorMaterials;
import net.minecraft.tags.DamageTypeTags;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.item.ArmorItem;

public class DMArmor extends PEArmor {

	public DMArmor(ArmorItem.Type armorPiece, Properties props) {
		super(PEArmorMaterials.DARK_MATTER, armorPiece, props);
	}

	@Override
	public float getFullSetBaseReduction() {
		return 0.8F;
	}

	@Override
	public float getMaxDamageAbsorb(ArmorItem.Type type, DamageSource source) {
		if (source.is(DamageTypeTags.IS_EXPLOSION)) {
			return 350;
		}
		if (type == ArmorItem.Type.BOOTS && source.is(DamageTypeTags.IS_FALL)) {
			return 5 / getPieceEffectiveness(type);
		} else if (type == ArmorItem.Type.HELMET && source.is(DamageTypeTags.IS_DROWNING)) {
			return 5 / getPieceEffectiveness(type);
		}
		if (source.is(DamageTypeTags.BYPASSES_ARMOR)) {
			return 0;
		}
		//If the source is not unblockable, allow our piece to block a certain amount of damage
		if (type == ArmorItem.Type.HELMET || type == ArmorItem.Type.BOOTS) {
			return 100;
		}
		return 150;
	}
}