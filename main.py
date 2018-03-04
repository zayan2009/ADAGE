import argparse
import random
import os
import torch.backends.cudnn as cudnn
import torch.optim as optim
import torch.utils.data
from torch.autograd import Variable
from dataset.data_loader import GetLoader
from torchvision import datasets
from torchvision import transforms

from logger import Logger
from models.model import CNNModel, Combo
import numpy as np
from test import test
import time


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--lr', type=float, default=1e-3)
    parser.add_argument('--image_size', type=int, default=28)
    parser.add_argument('--batch_size', default=128, type=int)
    parser.add_argument('--epochs', default=100, type=int)
    parser.add_argument('--DANN_weight', default=1.0, type=float)
    parser.add_argument('--use_deco', action="store_true", help="If true use deco architecture")
    return parser.parse_args()


def get_name(args):
    name = "lr:%g_batchSize:%d_epochs:%d_DannWeight:%g" % (args.lr, args.batch_size, args.epochs, args.DANN_weight)
    if args.use_deco:
        name += "_deco"
    return name + "_%d" % (time.time() % 100)


def to_np(x):
    return x.data.cpu().numpy()


def to_grid(x):
    # y = to_np(x).swapaxes(0, 1).reshape(3, 1, 28 * 3, 28 * 3).swapaxes(0, 1)
    y = to_np(x).swapaxes(1, 3).reshape(3, 28 * 3, 28, 3).swapaxes(1, 2).reshape(28 * 3, 28 * 3, 3)[np.newaxis, ...]
    print(y.shape)
    return y


args = get_args()
run_name = get_name(args)
logger = Logger("logs/" + run_name)

source_dataset_name = 'mnist'
target_dataset_name = 'mnist_m'
source_image_root = os.path.join('dataset', source_dataset_name)
target_image_root = os.path.join('dataset', target_dataset_name)
model_root = 'models'

cuda = True
cudnn.benchmark = True
lr = args.lr
batch_size = args.batch_size
image_size = args.image_size
n_epoch = args.epochs
dann_weight = args.DANN_weight

manual_seed = random.randint(1, 10000)
random.seed(manual_seed)
torch.manual_seed(manual_seed)

# load data

img_transform = transforms.Compose([
    # transforms.RandomResizedCrop(image_size),
    transforms.RandomCrop(image_size),
    transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, hue=0),
    transforms.ToTensor(),
    transforms.Normalize(mean=(0.5, 0.5, 0.5), std=(0.5, 0.5, 0.5))
])

dataset_source = datasets.MNIST(
    root=source_image_root,
    train=True,
    transform=img_transform, download=True
)

dataloader_source = torch.utils.data.DataLoader(
    dataset=dataset_source,
    batch_size=batch_size,
    shuffle=True,
    num_workers=4)

train_list = os.path.join(target_image_root, 'mnist_m_train_labels.txt')

dataset_target = GetLoader(
    data_root=os.path.join(target_image_root, 'mnist_m_train'),
    data_list=train_list,
    transform=img_transform
)

dataloader_target = torch.utils.data.DataLoader(
    dataset=dataset_target,
    batch_size=batch_size,
    shuffle=True,
    num_workers=4)

# load model

if args.use_deco:
    my_net = Combo()
else:
    my_net = CNNModel()

# setup optimizer

optimizer = optim.Adam(my_net.parameters(), lr=lr)

loss_class = torch.nn.NLLLoss()
loss_domain = torch.nn.NLLLoss()

if cuda:
    my_net = my_net.cuda()
    loss_class = loss_class.cuda()
    loss_domain = loss_domain.cuda()

for p in my_net.parameters():
    p.requires_grad = True

# training

for epoch in range(n_epoch):

    len_dataloader = min(len(dataloader_source), len(dataloader_target))
    data_source_iter = iter(dataloader_source)
    data_target_iter = iter(dataloader_target)

    i = 0
    while i < len_dataloader:

        p = float(i + epoch * len_dataloader) / n_epoch / len_dataloader
        alpha = 2. / (1. + np.exp(-10 * p)) - 1

        # training model using source data
        data_source = data_source_iter.next()
        s_img, s_label = data_source

        my_net.zero_grad()
        batch_size = len(s_label)

        input_img = torch.FloatTensor(batch_size, 3, image_size, image_size)
        class_label = torch.LongTensor(batch_size)
        domain_label = torch.zeros(batch_size)
        domain_label = domain_label.long()

        if cuda:
            s_img = s_img.cuda()
            s_label = s_label.cuda()
            input_img = input_img.cuda()
            class_label = class_label.cuda()
            domain_label = domain_label.cuda()

        input_img.resize_as_(s_img).copy_(s_img)
        class_label.resize_as_(s_label).copy_(s_label)
        inputv_img = Variable(input_img)
        classv_label = Variable(class_label)
        domainv_label = Variable(domain_label)

        class_output, domain_output = my_net(input_data=inputv_img, alpha=alpha)
        err_s_label = loss_class(class_output, classv_label)
        err_s_domain = loss_domain(domain_output, domainv_label)

        # training model using target data
        data_target = data_target_iter.next()
        t_img, _ = data_target

        batch_size = len(t_img)

        input_img = torch.FloatTensor(batch_size, 3, image_size, image_size)
        domain_label = torch.ones(batch_size)
        domain_label = domain_label.long()

        if cuda:
            t_img = t_img.cuda()
            input_img = input_img.cuda()
            domain_label = domain_label.cuda()

        input_img.resize_as_(t_img).copy_(t_img)
        inputv_img = Variable(input_img)
        domainv_label = Variable(domain_label)

        _, domain_output = my_net(input_data=inputv_img, alpha=alpha)
        err_t_domain = loss_domain(domain_output, domainv_label)
        err = dann_weight * err_t_domain + dann_weight * err_s_domain + err_s_label
        err.backward()
        optimizer.step()

        if (i is 0) and args.use_deco:
            logger.image_summary("images/source", to_grid(my_net.deco(Variable(s_img[:9]))), i + epoch * len_dataloader)
            logger.image_summary("images/target", to_grid(my_net.deco(Variable(t_img[:9]))), i + epoch * len_dataloader)

        i += 1

        if (i % 100) == 0:
            logger.scalar_summary("loss/source", err_s_label, i + epoch * len_dataloader)
            logger.scalar_summary("loss/domain_s", err_s_domain, i + epoch * len_dataloader)
            logger.scalar_summary("loss/domain_t", err_t_domain, i + epoch * len_dataloader)
            print('epoch: %d, [iter: %d / all %d], err_s_label: %f, err_s_domain: %f, err_t_domain: %f' \
                  % (epoch, i, len_dataloader, err_s_label.cpu().data.numpy(),
                     err_s_domain.cpu().data.numpy(), err_t_domain.cpu().data.numpy()))

    torch.save(my_net, '{0}/mnist_mnistm_model_epoch_{1}.pth'.format(model_root, epoch))
    s_acc = test(source_dataset_name, epoch)
    t_acc = test(target_dataset_name, epoch)
    logger.scalar_summary("acc/source", s_acc, i + epoch * len_dataloader)
    logger.scalar_summary("acc/target", t_acc, i + epoch * len_dataloader)

print('done')
