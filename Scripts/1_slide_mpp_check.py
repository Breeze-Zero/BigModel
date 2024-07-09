
import huggingface_hub
import os
from PuzzleAI.DataPipe.WSI_tools import find_level_for_target_mpp

# Please set your Hugging Face API token
os.environ["HF_TOKEN"] = "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"

assert "HF_TOKEN" in os.environ, "Please set the HF_TOKEN environment variable to your Hugging Face API token"

# put data there
local_dir = os.path.join('..', "demo/")
huggingface_hub.hf_hub_download("prov-gigapath/prov-gigapath", filename="sample_data/PROV-000-000001.ndpi",
                                local_dir=local_dir, force_download=True)
# load data
slide_path = os.path.join(local_dir, "sample_data/PROV-000-000001.ndpi")

print("NOTE: Prov-GigaPath is trained with 0.5 mpp preprocessed slides")
target_mpp = 0.5
# 好像代表等价到特定放大倍率之后的mpp (fixme still not sure, sq pls check, especially how to use TCGA in their case)
level = find_level_for_target_mpp(slide_path, target_mpp)
if level is not None:
    print(f"Found level: {level}")
else:
    print("No suitable level found.")
