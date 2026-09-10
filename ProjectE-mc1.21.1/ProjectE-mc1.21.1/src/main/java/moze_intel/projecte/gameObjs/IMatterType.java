package moze_intel.projecte.gameObjs;

import net.minecraft.world.item.Tier;

public interface IMatterType extends Tier {

	float getChargeModifier();

	int getMatterTier();
}