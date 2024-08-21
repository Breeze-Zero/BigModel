"""
Build WSI level models     Script  ver： Aug 21th 14:00
"""
import os
import torch
import torch.nn as nn
from typing import Optional, List
import huggingface_hub

try:
    from gigapath.slide_encoder import gigapath_slide_enc12l768d
except:
    from PuzzleAI.ModelBase.gigapath.slide_encoder import gigapath_slide_enc12l768d


def build_WSI_backbone_model(model_name='gigapath', local_weight_path=None,
                             ROI_feature_dim=1536, **kwargs):
    if model_name == 'gigapath':
        slide_backbone = gigapath_slide_enc12l768d(in_chans=ROI_feature_dim, global_pool=False, **kwargs)
        print("Slide encoder param #", sum(p.numel() for p in slide_backbone.parameters()))

        # download the weights
        if local_weight_path is None:
            # Hugging Face API token
            os.environ["HF_TOKEN"] = "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"
            hf_hub = "hf_hub:prov-gigapath/prov-gigapath"
            hub_name = hf_hub.split(":")[1]
            local_dir = os.path.join(os.path.expanduser("~"), ".cache/")

            huggingface_hub.hf_hub_download(hub_name, filename="slide_encoder.pth",
                                            local_dir=local_dir, force_download=True)
            local_weight_path = os.path.join(local_dir, "slide_encoder.pth")

        # build weight for slide level
        if os.path.exists(local_weight_path):
            state_dict = torch.load(local_weight_path, map_location="cpu")["model"]

            missing_keys, unexpected_keys = slide_backbone.load_state_dict(state_dict, strict=False)
            if len(missing_keys) > 0:
                for k in missing_keys:
                    print("Missing ", k)

            if len(unexpected_keys) > 0:
                for k in unexpected_keys:
                    print("Unexpected ", k)

            print("\033[92m Successfully Loaded Pretrained GigaPath model from {} \033[00m".format(local_weight_path))
        else:
            print("\033[93m Pretrained weights not found at {}. Randomly initialized the model! \033[00m".format(
                local_weight_path))

        return slide_backbone


class MTL_module_baseline(nn.Module):
    def __init__(self, MTL_token_num, latent_feature_dim):
        super().__init__()
        self.MTL_token_num, self.latent_feature_dim = MTL_token_num, latent_feature_dim
        self.layer = nn.Linear(latent_feature_dim, MTL_token_num * latent_feature_dim)

    def forward(self, latent_features):
        MTL_tokens = self.layer(latent_features).view(-1, self.MTL_token_num, self.latent_feature_dim)
        return MTL_tokens


class MTL_Model_builder(nn.Module):
    def __init__(self, backbone: nn.Module,
                 MTL_module: Optional[nn.Module] = None,
                 MTL_heads: List[nn.Module] = [nn.Identity(), nn.Identity(), nn.Identity()],
                 embed_dim: int = 768, latent_feature_dim: int = 128):
        """
        :param backbone: slide-level modeling model
        :param MTL_module: MTL model projecting the features to multiple task heads
        :param MTL_heads: the list of multiple WSI-level task heads for each task
        :param embed_dim: feature dim for MTL heads and WSI level modeling model
        :param latent_feature_dim: feature dim for MTL modeling
        """
        super().__init__()

        self.backbone = backbone  # the output feature is [B, embed_dim]

        if latent_feature_dim == embed_dim:
            self.embedding_converter = nn.Identity()
        else:
            self.embedding_converter = nn.Linear(embed_dim, latent_feature_dim)

        self.MTL_token_num = len(MTL_heads)
        assert self.MTL_token_num > 0, "MTL_heads cannot be empty."

        if MTL_module is None:
            self.MTL_module = MTL_module_baseline(self.MTL_token_num, latent_feature_dim)
        else:
            self.MTL_module = MTL_module

        self.MTL_heads = nn.ModuleList(MTL_heads)

    def forward(self, image_features, img_coords):
        """
        Forward pass for the MTL Transformer.

        :param image_features: Tensor of shape [B, N, feature_dim],
                               where B is batch size, N is the number of patches/features per slide.
        :return: List of task predictions, where each element in the list corresponds to a task and has
                 shape [B, output_dim] (output_dim may vary depending on the task).
        """
        slide_latent = self.backbone(image_features, img_coords)[0]  # fixme take from the list of embeddings
        MTL_tokens = self.MTL_module(self.embedding_converter(slide_latent))

        WSI_tasks_pred = []
        for idx, head in enumerate(self.MTL_heads):
            task_pred = head(MTL_tokens[:, idx])
            WSI_tasks_pred.append(task_pred)

        return WSI_tasks_pred


def build_WSI_task_model(model_name='gigapath', local_weight_path=None, ROI_feature_dim=1536,
                         MTL_heads=None,latent_feature_dim=128):
    assert MTL_heads is not None
    slide_backbone = build_WSI_backbone_model(model_name, local_weight_path, ROI_feature_dim)
    MTL_Model = MTL_Model_builder(slide_backbone, MTL_module=None, MTL_heads=MTL_heads,
                                  embed_dim=768,latent_feature_dim=latent_feature_dim)

    return MTL_Model
