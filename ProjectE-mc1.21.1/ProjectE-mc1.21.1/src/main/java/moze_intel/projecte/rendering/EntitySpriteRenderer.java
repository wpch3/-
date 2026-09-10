package moze_intel.projecte.rendering;

import com.mojang.blaze3d.vertex.PoseStack;
import com.mojang.blaze3d.vertex.PoseStack.Pose;
import com.mojang.blaze3d.vertex.VertexConsumer;
import net.minecraft.client.renderer.MultiBufferSource;
import net.minecraft.client.renderer.RenderType;
import net.minecraft.client.renderer.entity.EntityRenderer;
import net.minecraft.client.renderer.entity.EntityRendererProvider;
import net.minecraft.core.BlockPos;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.entity.Entity;
import org.jetbrains.annotations.NotNull;

/**
 * Based on {@link net.minecraft.client.renderer.entity.DragonFireballRenderer}
 */
public class EntitySpriteRenderer<ENTITY extends Entity> extends EntityRenderer<ENTITY> {

	private final ResourceLocation texture;
	private final RenderType renderType;

	public EntitySpriteRenderer(EntityRendererProvider.Context context, ResourceLocation texture) {
		super(context);
		this.texture = texture;
		this.renderType = PERenderType.SPRITE_RENDERER.apply(this.texture);
	}

	@Override
	protected int getBlockLightLevel(@NotNull ENTITY entity, @NotNull BlockPos pos) {
		return 15;
	}

	@NotNull
	@Override
	public ResourceLocation getTextureLocation(@NotNull ENTITY entity) {
		return texture;
	}

	@Override
	public void render(@NotNull ENTITY entity, float entityYaw, float partialTick, @NotNull PoseStack matrix, @NotNull MultiBufferSource renderer, int light) {
		matrix.pushPose();
		matrix.scale(0.5F, 0.5F, 0.5F);
		matrix.mulPose(entityRenderDispatcher.cameraOrientation());
		VertexConsumer builder = renderer.getBuffer(renderType);
		Pose pose = matrix.last();
		vertex(builder, pose, 0, 0, 0, 1);
		vertex(builder, pose, 1, 0, 1, 1);
		vertex(builder, pose, 1, 1, 1, 0);
		vertex(builder, pose, 0, 1, 0, 0);
		matrix.popPose();
	}

	private static void vertex(VertexConsumer consumer, PoseStack.Pose pose, float x, int y, int u, int v) {
		consumer.addVertex(pose, x - 0.5F, y, 0)
				.setUv(u, v);
	}
}