package moze_intel.projecte.gameObjs.registries;

import java.util.EnumMap;
import java.util.List;
import java.util.Map;
import moze_intel.projecte.PECore;
import moze_intel.projecte.gameObjs.registration.PEDeferredHolder;
import moze_intel.projecte.gameObjs.registration.PEDeferredRegister;
import net.minecraft.Util;
import net.minecraft.core.registries.Registries;
import net.minecraft.sounds.SoundEvents;
import net.minecraft.world.item.ArmorItem;
import net.minecraft.world.item.ArmorMaterial;
import net.minecraft.world.item.crafting.Ingredient;

public class PEArmorMaterials {

	private PEArmorMaterials() {
	}

	public static final PEDeferredRegister<ArmorMaterial> ARMOR_MATERIALS = new PEDeferredRegister<>(Registries.ARMOR_MATERIAL, PECore.MODID);
	private static final Map<ArmorItem.Type, Integer> DIAMOND_RESISTANCES = Util.make(new EnumMap<>(ArmorItem.Type.class), map -> {
		map.put(ArmorItem.Type.BOOTS, 3);
		map.put(ArmorItem.Type.LEGGINGS, 6);
		map.put(ArmorItem.Type.CHESTPLATE, 8);
		map.put(ArmorItem.Type.HELMET, 3);
		map.put(ArmorItem.Type.BODY, 11);
	});

	public static final PEDeferredHolder<ArmorMaterial, ArmorMaterial> DARK_MATTER = ARMOR_MATERIALS.register("dark_matter", rl -> new ArmorMaterial(
			DIAMOND_RESISTANCES, 0, SoundEvents.ARMOR_EQUIP_NETHERITE, () -> Ingredient.EMPTY, List.of(new ArmorMaterial.Layer(rl)), 2, 0.1F
	));
	public static final PEDeferredHolder<ArmorMaterial, ArmorMaterial> RED_MATTER = ARMOR_MATERIALS.register("red_matter", rl -> new ArmorMaterial(
			DIAMOND_RESISTANCES, 0, SoundEvents.ARMOR_EQUIP_NETHERITE, () -> Ingredient.EMPTY, List.of(new ArmorMaterial.Layer(rl)), 2, 0.2F
	));
	public static final PEDeferredHolder<ArmorMaterial, ArmorMaterial> GEM_ARMOR = ARMOR_MATERIALS.register("gem_armor", rl -> new ArmorMaterial(
			DIAMOND_RESISTANCES, 0, SoundEvents.ARMOR_EQUIP_NETHERITE, () -> Ingredient.EMPTY, List.of(new ArmorMaterial.Layer(rl)), 2, 0.25F
	));
}