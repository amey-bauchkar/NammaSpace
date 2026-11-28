"""Fixed-density regional optimisation with immutable context and stable source row indices."""
import torch


class TwoStore:
    def __init__(self, parameters, lower, upper, boundary_m=.05, proposal=None):
        self.lower = torch.as_tensor(lower,device=parameters["means"].device,dtype=parameters["means"].dtype)
        self.upper = torch.as_tensor(upper,device=self.lower.device,dtype=self.lower.dtype)
        if self.lower.shape!=(3,) or self.upper.shape!=(3,) or not torch.isfinite(self.lower).all() or not (
                torch.isfinite(self.upper).all() and (self.upper>self.lower).all()) or boundary_m<=0:
            raise ValueError("invalid regional bounds")
        n = len(parameters["means"])
        if any(len(p)!=n or not torch.isfinite(p).all() for p in parameters.values()):
            raise ValueError("invalid Gaussian parameter rows")
        self.original = {k:v.detach().clone() for k,v in parameters.items()}
        means = self.original["means"]
        mask = ((means>=self.lower)&(means<=self.upper)).all(-1)
        self.ids = mask.nonzero().flatten()
        self.frozen_ids = (~mask).nonzero().flatten()
        if not len(self.ids):
            raise ValueError("region contains no Gaussian centres")
        seed = {k:v[self.ids].clone() for k,v in self.original.items()} if proposal is None else proposal
        if set(seed)!=set(self.original) or any(v.shape[1:]!=self.original[k].shape[1:] or
                len(v)!=len(seed["means"]) or not torch.isfinite(v).all() for k,v in seed.items()):
            raise ValueError("proposal Gaussian schema differs from source")
        if not len(seed["means"]) or ((seed["means"]<self.lower)|(seed["means"]>self.upper)).any():
            raise ValueError("proposal centres outside the trainable region")
        self.proposal = proposal is not None
        self.anchors = {k:v.detach().clone() for k,v in seed.items()}
        if self.proposal:
            from scipy.spatial import cKDTree
            nearest = cKDTree(means[self.ids].cpu().numpy()).query(seed["means"].detach().cpu().numpy())[1]
            nearest = torch.as_tensor(nearest,device=self.ids.device)
            self.anchors = {k:v[self.ids][nearest].clone() for k,v in self.original.items()}
        self.trainable = torch.nn.ParameterDict({k:torch.nn.Parameter(v.detach().clone()) for k,v in seed.items()})
        edge_distance = torch.minimum(seed["means"]-self.lower,self.upper-seed["means"]).min(-1).values
        self.boundary = edge_distance<boundary_m

    def parameters(self):
        if self.proposal:
            return {k:torch.cat([v[self.frozen_ids],self.trainable[k]],0) for k,v in self.original.items()}
        return {k:torch.index_copy(v,0,self.ids,self.trainable[k]) for k,v in self.original.items()}

    def boundary_loss(self):
        if not self.boundary.any():
            return self.trainable["means"].sum()*0
        return sum((v[self.boundary]-self.anchors[k][self.boundary]).square().mean()
                   for k,v in self.trainable.items())

    def enforce(self, max_scale_m=.2):
        if max_scale_m<=0:
            raise ValueError("positive regional scale bound required")
        import math
        with torch.no_grad():
            self.trainable["means"].clamp_(min=self.lower,max=self.upper)
            self.trainable["scales"].clamp_(max=math.log(max_scale_m))
        if any(not torch.isfinite(v).all() for v in self.trainable.values()):
            raise ValueError("regional optimisation diverged")

    def assert_frozen(self):
        current = self.parameters()
        if any(not torch.equal(v[:len(self.frozen_ids)] if self.proposal else v[self.frozen_ids],
                               self.original[k][self.frozen_ids]) for k,v in current.items()):
            raise ValueError("frozen Gaussian rows changed")
