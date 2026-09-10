package moze_intel.projecte.rendering;

import com.mojang.blaze3d.vertex.PoseStack;
import com.mojang.blaze3d.vertex.PoseStack.Pose;
import com.mojang.blaze3d.vertex.VertexConsumer;
import com.mojang.math.Axis;
import java.util.UUID;
import moze_intel.projecte.PECore;
import net.minecraft.client.model.PlayerModel;
import net.minecraft.client.player.AbstractClientPlayer;
import net.minecraft.client.renderer.MultiBufferSource;
import net.minecraft.client.renderer.entity.layers.RenderLayer;
import net.minecraft.client.renderer.entity.player.PlayerRenderer;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.util.Mth;
import net.neoforged.fml.loading.FMLEnvironment;
import org.jetbrains.annotations.NotNull;

public class LayerYue extends RenderLayer<AbstractClientPlayer, PlayerModel<AbstractClientPlayer>> {

	private static final UUID SIN_UUID = UUID.fromString("5f86012c-ca4b-451a-989c-8fab167af647");
	private static final UUID CLAR_UUID = UUID.fromString("e5c59746-9cf7-4940-a849-d09e1f1efc13");
	private static final ResourceLocation HEART_LOC = PECore.rl("textures/models/heartcircle.png");
	private static final ResourceLocation YUE_LOC = PECore.rl("textures/models/yuecircle.png");

	public LayerYue(PlayerRenderer renderer) {
		super(renderer);
	}

	@Override
	public void render(@NotNull PoseStack matrix, @NotNull MultiBufferSource renderer, int light, @NotNull AbstractClientPlayer player,
			float limbSwing, float limbSwingAmount, float partialTicks, float ageInTicks, float netHeadYaw, float headPitch) {
		if (player.isInvisible()) {
			return;
		}
		if (!FMLEnvironment.production || SIN_UUID.equals(player.getUUID()) || CLAR_UUID.equals(player.getUUID())) {
			matrix.pushPose();
			getParentModel().body.translateAndRotate(matrix);
			double yShift = -0.498;
			if (player.isCrouching()) {
				//Only modify where it renders if the player's pose is crouching
				matrix.mulPose(Axis.XN.rotation(0.5F));
				yShift = -0.44;
			}
			matrix.mulPose(Axis.ZP.rotation(Mth.PI));
			matrix.scale(3, 3, 3);
			matrix.translate(-0.5, yShift, -0.5);
			VertexConsumer builder = renderer.getBuffer(PERenderType.YEU_RENDERER.apply(getTextureLocation(player)));
			Pose pose = matrix.last();
			builder.addVertex(pose, 0, 0, 0).setUv(0, 0).setColor(0, 255, 0, 255);
			builder.addVertex(pose, 0, 0, 1).setUv(0, 1).setColor(0, 255, 0, 255);
			builder.addVertex(pose, 1, 0, 1).setUv(1, 1).setColor(0, 255, 0, 255);
			builder.addVertex(pose, 1, 0, 0).setUv(1, 0).setColor(0, 255, 0, 255);
			matrix.popPose();
		}
	}

	@NotNull
	@Override
	protected ResourceLocation getTextureLocation(AbstractClientPlayer player) {
		return CLAR_UUID.equals(player.getUUID()) ? HEART_LOC : YUE_LOC;
	}
}