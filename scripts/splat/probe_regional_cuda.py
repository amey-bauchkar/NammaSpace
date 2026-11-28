"""Exercise frozen and trainable Gaussian rows with the installed CUDA renderer."""
import json

import gsplat
import torch
from gsplat import DefaultStrategy, rasterization

from regional_strategy import regional_strategy_info


def probe(action):
    device = torch.device("cuda")
    frozen = torch.tensor([[-0.2, 0., 2.]], device=device)
    original = frozen.clone()
    trainable = torch.nn.Parameter(-frozen.clone())
    with torch.no_grad():
        trainable[:, 2] = 2.
    before = trainable.detach().clone()
    optimizer = torch.optim.Adam([trainable], lr=.001)
    view = torch.eye(4, device=device)[None]
    intrinsics = torch.tensor([[[64., 0., 32.], [0., 64., 32.], [0., 0., 1.]]], device=device)
    params = {"means": trainable,
              "scales": torch.nn.Parameter(torch.full_like(trainable, .12).log()),
              "opacities": torch.nn.Parameter(torch.tensor([1.4], device=device)),
              "quats": torch.nn.Parameter(torch.tensor([[1., 0., 0., 0.]], device=device))}
    context = {key: value.detach().clone() for key, value in params.items()}
    context["means"] = frozen
    originals = {key: value.clone() for key, value in context.items()}
    if action == "prune":
        with torch.no_grad():
            params["opacities"].fill_(-5.)
    combined = {key: torch.cat([context[key], value]) for key, value in params.items()}
    colors, alpha, info = rasterization(
        combined["means"], combined["quats"], combined["scales"].exp(),
        combined["opacities"].sigmoid(), torch.ones_like(combined["means"]),
        view, intrinsics, 64, 64, packed=False, absgrad=True, rasterize_mode="antialiased")
    optimizers = {name: torch.optim.Adam([value]) for name, value in params.items()}
    optimizers["means"] = optimizer
    strategy = DefaultStrategy(prune_opa=.01, absgrad=True, refine_start_iter=0, refine_every=1,
                               refine_stop_iter=2, reset_every=3, grow_scale3d=.01 if action == "split" else 1.,
                               grow_grad2d=float("inf") if action == "prune" else 0.)
    state = strategy.initialize_state(scene_scale=1.)
    strategy.check_sanity(params, optimizers)
    strategy.step_pre_backward(params, optimizers, state, 1, info)
    colors[:, :, 32:].sum().backward()
    projected = info["means2d"].absgrad
    if not torch.isfinite(projected).all() or projected[:, 1].sum() <= 0:
        raise RuntimeError("missing trainable screen-space gradient")
    for opt in optimizers.values():
        opt.step()
    if not torch.equal(frozen, original) or torch.equal(trainable, before) or not torch.isfinite(trainable).all():
        raise RuntimeError("two-store update failed")
    regional = regional_strategy_info(info, len(frozen), len(trainable))
    strategy.step_post_backward(params, optimizers, state, 1, regional)
    expected = 0 if action == "prune" else 2
    if len(params["means"]) != expected or any(
            not torch.equal(context[key], value) for key, value in originals.items()):
        raise RuntimeError("regional strategy integrity or count failed")
    return {"regional_rows_after_growth": len(params["means"]), "device": torch.cuda.get_device_name(), "torch": torch.__version__,
            "gsplat": gsplat.__version__, "alpha_max": alpha.max().item(),
            "trainable_absgrad": projected[:, 1].sum().item(),
            "frozen_equal": True, "trainable_changed": True}


if __name__ == "__main__":
    print(json.dumps({action: probe(action) for action in ("duplicate", "split", "prune")}, indent=2))
