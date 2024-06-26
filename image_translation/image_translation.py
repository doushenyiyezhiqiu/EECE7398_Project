import argparse
import os
import numpy as np

# import requests
# from io import BytesIO
from PIL import Image

# import math
import itertools
import datetime
import time

# import torchvision
import torchvision.transforms as transforms
from torchvision.utils import save_image, make_grid

from torch.utils.data import DataLoader
from torchvision import datasets
from torch.autograd import Variable

from models import *
from datasets import *
from utils import *
import models_mae

# import torch.nn as nn
# import torch.nn.functional as F
import torch
# import matplotlib.pyplot as plt

def sample_images(Tensor, opt, batches_done, val_dataloader, G_AB, G_BA):
    """Saves a generated sample from the test set"""
    imgs = next(iter(val_dataloader))
    # G_AB.eval()
    # G_BA.eval()
    with torch.no_grad():
        real_A = Variable(imgs["A"].type(Tensor)) # n, c, h, w
        _, fake_B, _ = G_AB(real_A.float(), 0.5)
        fake_B = G_AB.unpatchify(fake_B) # n, c, h, w
        real_B = Variable(imgs["B"].type(Tensor))
        _, fake_A, _ = G_BA(real_B.float(), 0.5)
        fake_A = G_BA.unpatchify(fake_A)
    # Arange images along x-axis
    real_A = make_grid(real_A, nrow=opt.val_batch_size, normalize=True)
    real_B = make_grid(real_B, nrow=opt.val_batch_size, normalize=True)
    fake_A = make_grid(fake_A, nrow=opt.val_batch_size, normalize=True)
    fake_B = make_grid(fake_B, nrow=opt.val_batch_size, normalize=True)
    # Arange images along y-axis
    image_grid = torch.cat((real_A, fake_B, real_B, fake_A), 1) #, real_B, fake_A
    save_image(image_grid, "images/%s/%s.png" % (opt.dataset_name, batches_done), normalize=False)
    print('print out sample ' + str(batches_done))

def train(opt, dataloader,val_dataloader,Tensor, G_AB, G_BA, D_B):
    # ----------
    #  Training
    # ----------
    # Optimizers
    optimizer_G = torch.optim.Adam(
        itertools.chain(G_AB.parameters(), G_BA.parameters()), lr=opt.lr, betas=(opt.b1, opt.b2)
    )
    optimizer_D_A = torch.optim.Adam(D_A.parameters(), lr=opt.lr, betas=(opt.b1, opt.b2))
    optimizer_D_B = torch.optim.Adam(D_B.parameters(), lr=opt.lr, betas=(opt.b1, opt.b2))

    # Learning rate update schedulers
    lr_scheduler_G = torch.optim.lr_scheduler.LambdaLR(
        optimizer_G, lr_lambda=LambdaLR(opt.n_epochs, opt.epoch, opt.decay_epoch).step
    )
    lr_scheduler_D_A = torch.optim.lr_scheduler.LambdaLR(
        optimizer_D_A, lr_lambda=LambdaLR(opt.n_epochs, opt.epoch, opt.decay_epoch).step
    )
    lr_scheduler_D_B = torch.optim.lr_scheduler.LambdaLR(
        optimizer_D_B, lr_lambda=LambdaLR(opt.n_epochs, opt.epoch, opt.decay_epoch).step
    )

    # Buffers of previously generated samples
    fake_A_buffer = ReplayBuffer()
    fake_B_buffer = ReplayBuffer()

    prev_time = time.time()
    for epoch in range(opt.epoch, opt.epoch + opt.n_epochs):
        for i, batch in enumerate(dataloader):

            # Set model input
            real_A = Variable(batch["A"].type(Tensor)) # nchw
            real_B = Variable(batch["B"].type(Tensor))

            # Adversarial ground truths for patchGAN
            valid = Variable(Tensor(np.ones((real_A.size(0), *D_A.output_shape))), requires_grad=False) # all one
            fake = Variable(Tensor(np.zeros((real_A.size(0), *D_A.output_shape))), requires_grad=False) # all zero

            # ------------------
            #  Train Generators
            # ------------------

            G_AB.train()
            G_BA.train()

            optimizer_G.zero_grad()

            # Identity loss
            _, generate_A, _ = G_BA(real_A.float(), 0.5)
            loss_id_A = criterion_identity(G_BA.unpatchify(generate_A), real_A)
            _, generate_B, _ = G_AB(real_B.float(), 0.5)
            loss_id_B = criterion_identity(G_AB.unpatchify(generate_B), real_B)

            loss_identity = (loss_id_A + loss_id_B) / 2

            # GAN loss
            # in the procedure of training generator, we hope the fake image generated by generator would be classified as true
            # at this moment, we just use discriminator to classify the output of generator, not train discriminator
            _, fake_B, _ = G_AB(real_A.float(), 0.5)
            fake_B = G_AB.unpatchify(fake_B)
            loss_GAN_AB = criterion_GAN(D_B(fake_B), valid)
            _, fake_A, _ = G_BA(real_B.float(), 0.5)
            fake_A = G_BA.unpatchify(fake_A)
            loss_GAN_BA = criterion_GAN(D_A(fake_A), valid)

            loss_GAN = (loss_GAN_AB + loss_GAN_BA) / 2

            # Cycle loss
            _, recov_A, _ = G_BA(fake_B.float(), 0.5)
            recov_A = G_BA.unpatchify(recov_A)
            loss_cycle_A = criterion_cycle(recov_A, real_A)
            _, recov_B, _ = G_AB(fake_A.float(), 0.5)
            recov_B = G_AB.unpatchify(recov_B)
            loss_cycle_B = criterion_cycle(recov_B, real_B)

            loss_cycle = (loss_cycle_A + loss_cycle_B) / 2

            # Total loss
            loss_G = loss_GAN + opt.lambda_cyc * loss_cycle + opt.lambda_id * loss_identity

            loss_G.backward()
            optimizer_G.step()

            # -----------------------
            #  Train Discriminator A
            #  the procedure of training discriminator is to classify real image as true, classify fake image as fake
            # -----------------------

            optimizer_D_A.zero_grad()

            # Real loss
            loss_real = criterion_GAN(D_A(real_A), valid)
            # Fake loss (on batch of previously generated samples)
            fake_A_ = fake_A_buffer.push_and_pop(fake_A)
            loss_fake = criterion_GAN(D_A(fake_A_.detach()), fake)
            # Total loss
            loss_D_A = (loss_real + loss_fake) / 2

            loss_D_A.backward()
            optimizer_D_A.step()

            # -----------------------
            #  Train Discriminator B
            # -----------------------

            optimizer_D_B.zero_grad()

            # Real loss
            loss_real = criterion_GAN(D_B(real_B), valid)
            # Fake loss (on batch of previously generated samples)
            fake_B_ = fake_B_buffer.push_and_pop(fake_B)
            loss_fake = criterion_GAN(D_B(fake_B_.detach()), fake)
            # Total loss
            loss_D_B = (loss_real + loss_fake) / 2

            loss_D_B.backward()
            optimizer_D_B.step()

            loss_D = (loss_D_A + loss_D_B) / 2

            # --------------
            #  Log Progress
            # --------------

            # Determine approximate time left
            batches_done = epoch * len(dataloader) + i
            batches_left = opt.n_epochs * len(dataloader) - batches_done
            time_left = datetime.timedelta(seconds=batches_left * (time.time() - prev_time))
            prev_time = time.time()

            # Print log
            sys.stdout.write(
                "\r[Epoch %d/%d] [Batch %d/%d] [D loss: %f] [G loss: %f, adv: %f, cycle: %f, identity: %f] ETA: %s"
                % (
                    epoch,
                    opt.n_epochs,
                    i,
                    len(dataloader),
                    loss_D.item(),
                    loss_G.item(),
                    loss_GAN.item(),
                    loss_cycle.item(),
                    loss_identity.item(),
                    time_left,
                )
            )

            # If at sample interval save image
            if (batches_done+1) % opt.sample_interval == 0:
                sample_images(Tensor, opt, batches_done+1, val_dataloader, G_AB, G_BA)

        # Update learning rates
        lr_scheduler_G.step()
        lr_scheduler_D_A.step()
        lr_scheduler_D_B.step()

        if opt.checkpoint_interval != -1 and epoch % opt.checkpoint_interval == 0:
            # Save model checkpoints
            torch.save(G_AB.state_dict(), "saved_models/%s/G_AB_%d.pth" % (opt.dataset_name, epoch+1))
            torch.save(G_BA.state_dict(), "saved_models/%s/G_BA_%d.pth" % (opt.dataset_name, epoch+1))
            torch.save(D_A.state_dict(), "saved_models/%s/D_A_%d.pth" % (opt.dataset_name, epoch+1))
            torch.save(D_B.state_dict(), "saved_models/%s/D_B_%d.pth" % (opt.dataset_name, epoch+1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--epoch", type=int, default=0, help="epoch to start training from")
    parser.add_argument("--n_epochs", type=int, default=200, help="number of epochs of training")
    parser.add_argument("--dataset_name", type=str, default="monet2photo", help="name of the dataset")
    parser.add_argument("--batch_size", type=int, default=64, help="size of the training batches")
    parser.add_argument("--val_batch_size", type=int, default=5, help="size of the testing batches")
    parser.add_argument("--lr", type=float, default=0.0002, help="adam: learning rate")
    parser.add_argument("--b1", type=float, default=0.5, help="adam: decay of first order momentum of gradient")
    parser.add_argument("--b2", type=float, default=0.999, help="adam: decay of first order momentum of gradient")
    parser.add_argument("--decay_epoch", type=int, default=100, help="epoch from which to start lr decay")
    parser.add_argument("--n_cpu", type=int, default=1, help="number of cpu threads to use during batch generation")
    parser.add_argument("--img_height", type=int, default=128, help="size of image height")
    parser.add_argument("--img_width", type=int, default=128, help="size of image width")
    parser.add_argument("--channels", type=int, default=3, help="number of image channels")
    parser.add_argument("--sample_interval", type=int, default=100, help="interval between saving generator outputs")
    parser.add_argument("--checkpoint_interval", type=int, default=-1, help="interval between saving model checkpoints")
    parser.add_argument("--n_residual_blocks", type=int, default=9, help="number of residual blocks in generator")
    parser.add_argument("--lambda_cyc", type=float, default=10.0, help="cycle loss weight")
    parser.add_argument("--lambda_id", type=float, default=5.0, help="identity loss weight")
    opt = parser.parse_args()

    # Create sample and checkpoint directories
    os.makedirs("images/%s" % opt.dataset_name, exist_ok=True)
    os.makedirs("saved_models/%s" % opt.dataset_name, exist_ok=True)

    cuda = torch.cuda.is_available()
    Tensor = torch.cuda.FloatTensor if cuda else torch.Tensor
    # Losses
    criterion_GAN = torch.nn.MSELoss()
    criterion_cycle = torch.nn.L1Loss()
    criterion_identity = torch.nn.L1Loss()

    # Initialize generator and discriminator
    input_shape = (opt.channels, opt.img_height, opt.img_width)
    # G_AB = GeneratorResNet(input_shape, opt.n_residual_blocks)
    G_AB = models_mae.mae_vit_base_patch16_dec512d8b()
    # G_BA = GeneratorResNet(input_shape, opt.n_residual_blocks)
    G_BA = models_mae.mae_vit_base_patch16_dec512d8b()
    D_A = Discriminator(input_shape)
    D_B = Discriminator(input_shape)

    if cuda:
        G_AB = G_AB.cuda()
        G_BA = G_BA.cuda()
        D_A = D_A.cuda()
        D_B = D_B.cuda()
        criterion_GAN.cuda()
        criterion_cycle.cuda()
        criterion_identity.cuda()

    if opt.epoch != 0:
        # Load pretrained models
        G_AB.load_state_dict(torch.load("saved_models/%s/G_AB_%d.pth" % (opt.dataset_name, opt.epoch)))
        G_BA.load_state_dict(torch.load("saved_models/%s/G_BA_%d.pth" % (opt.dataset_name, opt.epoch)))
        D_A.load_state_dict(torch.load("saved_models/%s/D_A_%d.pth" % (opt.dataset_name, opt.epoch)))
        D_B.load_state_dict(torch.load("saved_models/%s/D_B_%d.pth" % (opt.dataset_name, opt.epoch)))
    else:
        D_A.apply(weights_init_normal)
        D_B.apply(weights_init_normal)

    # Image transformations
    transforms_ = [
        transforms.Resize(int(opt.img_height * 1.12), Image.BICUBIC),
        transforms.RandomCrop((opt.img_height, opt.img_width)),
        # transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
    ]
    # Training data loader
    dataloader = DataLoader(
        ImageDataset("./data/%s" % opt.dataset_name, transforms_=transforms_, unaligned=True),
        batch_size=opt.batch_size,
        shuffle=True,
        # num_workers=opt.n_cpu,
    )
    # Test data loader
    val_dataloader = DataLoader(
        ImageDataset("./data/%s" % opt.dataset_name, transforms_=transforms_, unaligned=True, mode="test"),
        batch_size=opt.val_batch_size,
        shuffle=True,
        # num_workers=1,
    )

    train(opt, dataloader,val_dataloader,Tensor, G_AB, G_BA, D_B)