import random
import torch

import os
from matplotlib import pyplot as plt
from modules.diffusion import Diffusion
from modules.utils import plot_images, test_save_images,make_stroke,stroke_to_char
from models.utils import UNet





num_classes = 420
input_length = 100
contents_dim = 100
input_size = 64
mode = "random"
folder_name ="test_2"

if __name__ == '__main__':
    os.environ['CUDA_VISIBLE_DEVICES'] = str(0)
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    model = UNet().to(device)
    ckpt = torch.load("/home/hojun/PycharmProjects/diffusion_font/code/KoFont-Diffusion/hojun/results/models/font_noStrokeStyle_Unet64_image420_6/ckpt_90.pt")
    model.load_state_dict(ckpt)

    diffusion = Diffusion(first_beta=1e-4,
                              end_beta=0.02,
                              noise_step=1000,
                              beta_schedule_type='linear',
                              img_size=input_size,
                              device=device)

    if mode == "random":
        contents_emb = torch.zeros(input_length,contents_dim)

        first= [random.randint(0,18) for _ in range(input_length)]
        middle = [random.randint(19,39) for _ in range(input_length)]
        last = [random.randint(40,67) for _ in range(input_length)]

        strokes = torch.Tensor([[0 for _ in range(68)] for _ in range(input_length)])

        for idx in range(input_length):
            strokes[idx][first[idx]], strokes[idx][middle[idx]], strokes[idx][last[idx]] = 1, 1, 1
        char_list = stroke_to_char(strokes)

        style_emb = torch.zeros(input_length,12288)

        y = torch.cat([contents_emb, strokes, style_emb], dim=1).to(device)
        x = diffusion.test_sampling(model, input_length, y, cfg_scale=3)

    elif mode == "manual":
        char_list = ['가,나,다,라,마,바,사,아,자,차,카,타,파,하']
        contents_emb = torch.zeros(input_length, contents_dim)
        strokes = make_stroke(char_list)
        style_emb = torch.zeros(input_length, 12288)
        y = torch.cat([contents_emb, strokes, style_emb], dim=1).to(device)
        x = diffusion.test_sampling(model,len(strokes), y, cfg_scale=3)
    # plot_images(x)
    test_save_images(x, char_list,folder_name)
