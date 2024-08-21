'''
MTL training panel       Script  ver： Aug 21th 14:00
'''
import os
import sys
from pathlib import Path

# For convinience
this_file_dir = Path(__file__).resolve().parent
sys.path.append(str(this_file_dir.parent.parent.parent))  # Go up 3 levels

import torch

try:
    from DownStream.MTL.slide_dataset_tools import *
    from DownStream.MTL.Dataset_Framework import *
    from ModelBase.Get_WSI_model import build_WSI_task_model
    from DownStream.MTL.Task_settings import *
except:
    from PuzzleAI.DownStream.MTL.slide_dataset_tools import *
    from PuzzleAI.DownStream.MTL.Dataset_Framework import *
    from PuzzleAI.ModelBase.Get_WSI_model import build_WSI_task_model
    from PuzzleAI.ModelBase.Task_settings import *

if __name__ == '__main__':
    root_path = '/data/BigModel/embedded_datasets/'
    task_description_csv = \
        '/home/zhangty/Desktop/BigModel/prov-gigapath/PuzzleAI/Archive/dataset_csv/TCGA_Log_Transcriptome_Final.csv'
    slide_id_key = 'patient_id'
    split_target_key = 'fold_information'
    task_setting_folder_name = 'task-settings'
    latent_feature_dim = 128
    mode = 'TCGA'

    build_data_split_for_csv(task_description_csv, slide_id_key=slide_id_key, test_ratio=0.2, k=1,
                             mode=mode, key=split_target_key)
    task_settings_path = os.path.join(root_path, task_setting_folder_name)
    build_yaml_config_from_csv(task_description_csv, task_settings_path, dataset_name='lung-mix',
                               tasks_to_run=['CMS', 'COL3A1'],
                               max_tiles=1000000, shuffle_tiles=True,
                               excluding_list=(slide_id_key, split_target_key))
    # instantiate the dataset
    Train_dataset = SlideDataset(root_path, task_description_csv,
                                 task_setting_folder_name=task_setting_folder_name,
                                 split_name='train', slide_id_key=slide_id_key,
                                 split_target_key=split_target_key,mode=mode)
    Val_dataset = SlideDataset(root_path, task_description_csv,
                               task_setting_folder_name=task_setting_folder_name,
                               split_name='val', slide_id_key=slide_id_key,
                               split_target_key=split_target_key, mode=mode)
    Test_dataset = SlideDataset(root_path, task_description_csv,
                               task_setting_folder_name=task_setting_folder_name,
                               split_name='test', slide_id_key=slide_id_key,
                               split_target_key=split_target_key, mode=mode)

    # print(Train_dataset.get_embedded_sample_with_try(20))
    dataloaders = {
        'Train': torch.utils.data.DataLoader(Train_dataset, batch_size=1,
                                             collate_fn=MTL_WSI_collate_fn,
                                             shuffle=True, num_workers=2, drop_last=True),
        'Val': torch.utils.data.DataLoader(Val_dataset, batch_size=1,
                                           collate_fn=MTL_WSI_collate_fn,
                                           shuffle=False, num_workers=2, drop_last=True),
        'Test': torch.utils.data.DataLoader(Test_dataset, batch_size=1,
                                           collate_fn=MTL_WSI_collate_fn,
                                           shuffle=False, num_workers=2, drop_last=True)}
    # print(sample)
    '''
    sample = {'image_features': image features [N, D] tensor,
              'image_features_lens': data_dict['image_features_lens'],
              'pad_mask': data_dict['pad_mask'],
              'coords_yx': [N, 2] tensor,
              'slide_id': slide_id,
              'task_name_list': task_name_list,
              'task_description_list': task_description_list}
    '''

    task_config_path = os.path.join(task_settings_path, 'task_configs.yaml')
    WSI_task_dict, MTL_heads, WSI_criterions, loss_weight, class_num, WSI_task_describe = \
        task_filter_auto(task_config_path, latent_feature_dim=latent_feature_dim)
    print('WSI_task_dict', WSI_task_dict)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    model = build_WSI_task_model(model_name='gigapath', local_weight_path=None, ROI_feature_dim=1536,
                                 MTL_heads=MTL_heads, latent_feature_dim=latent_feature_dim)
    model = model.to(device)
    model = torch.compile(model)
    model.eval()  # Set model to evaluation mode

    sample_count=0
    failed_sample=[]

    for phase in ['Train', 'Val', 'Test']:
        # example with a sample
        for batch_sample in dataloaders[phase]:
            image_features, coords_yx, task_description_list, slide_id = batch_sample
            print('slide predication with a sample:', slide_id)

            try:
                image_features = image_features.to(device, non_blocking=True)
                coords_yx = coords_yx.to(device, non_blocking=True)
                # label = label.to(device, non_blocking=True).long()

                with torch.no_grad():  # No need for gradient computation during embedding

                    with torch.cuda.amp.autocast(dtype=torch.float16):
                        slide_embeds = model(image_features, coords_yx)
                        # layer_outputs = {"layer_{}_embed".format(i): slide_embeds[i].cpu() for i in range(len(slide_embeds))}
                        # layer_outputs["last_layer_embed"] = slide_embeds[-1].cpu()
                    print(slide_embeds)
                    sample_count += 1
            except:
                failed_sample.append(slide_id)
    print('**********************************************************')
    print('embedding tested valid sample num:', sample_count, '\n',
          'embedding tested failed sample:', failed_sample)
