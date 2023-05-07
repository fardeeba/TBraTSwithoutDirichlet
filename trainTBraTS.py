import os
import torch
import torch.optim as optim
import time
from torch.autograd import Variable
from torch.utils.data import DataLoader
from trustedseg import TMSU
from myBraTSData import BraTS
from predict import tailor_and_concat,softmax_mIOU_score,softmax_output_dice
import torch.nn.functional as F
import numpy as np
import matplotlib.pyplot as plt
from torch.utils.tensorboard import SummaryWriter
device='cuda' if torch.cuda.is_available() else 'cpu'

class AverageMeter(object):
    """Computes and stores the average and current value"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    # Basic Information
    local_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    parser.add_argument('--user', default='name of user', type=str)
    parser.add_argument('--experiment', default='TBraTS', type=str)
    parser.add_argument('--date', default=local_time.split(' ')[0], type=str)
    parser.add_argument('--description',
                        default='Trusted brain tumor segmentation by coco,'
                                'training on train.txt!',
                        type=str)
    # training detalis
    parser.add_argument('--epochs', type=int, default=199, metavar='N',
                        help='number of epochs to train [default: 500]')
    parser.add_argument('--test_epoch', type=int, default=198, metavar='N',
                        help='best epoch')
    parser.add_argument('--lambda-epochs', type=int, default=50, metavar='N',
                        help='gradually increase the value of lambda from 0 to 1')
    parser.add_argument('--save_freq', default=1, type=int)
    parser.add_argument('--lr', type=float, default=0.002, metavar='LR',
                        help='learning rate')
    # DataSet Information
    parser.add_argument('--root', default='/kaggle/input/brats2019/DataSet', type=str)
    parser.add_argument('--save_dir', default='./results', type=str)
    parser.add_argument('--train_dir', default='MICCAI_BraTS_2019_Data_TTraining', type=str)
    parser.add_argument('--valid_dir', default='MICCAI_BraTS_2019_Data_TValidation', type=str)
    parser.add_argument('--test_dir', default='MICCAI_BraTS_2019_Data_TTest', type=str)
    parser.add_argument("--mode", default="train", type=str, help="train/test/train&test")
    parser.add_argument('--train_file',
                        default='/kaggle/input/brats2019/DataSet/MICCAI_BraTS_2019_Data_Training/Ttrain_subject.txt',
                        type=str)
    parser.add_argument('--valid_file',
                        default='/kaggle/input/brats2019/DataSet/MICCAI_BraTS_2019_Data_Training/Tval_subject.txt',
                        type=str)
    parser.add_argument('--test_file',
                         default='/kaggle/input/brats2019/DataSet/MICCAI_BraTS_2019_Data_Training/Ttest_subject.txt',
                         type=str)
    parser.add_argument('--dataset', default='brats', type=str)
    parser.add_argument('--classes', default=4, type=int)# brain tumor class
    parser.add_argument('--input_H', default=240, type=int)
    parser.add_argument('--input_W', default=240, type=int)
    parser.add_argument('--input_D', default=160, type=int)  # 155
    parser.add_argument('--crop_H', default=128, type=int)
    parser.add_argument('--crop_W', default=128, type=int)
    parser.add_argument('--crop_D', default=128, type=int)
    parser.add_argument('--output_D', default=155, type=int)
    parser.add_argument('--batch_size', default=4, type=int, help="2/4/8")
    parser.add_argument('--input_dims', default='four', type=str)  # multi-modal/Single-modal
    parser.add_argument('--model_name', default='V', type=str)  # multi-modal V:168 AU:197
    parser.add_argument('--Variance', default=0.5, type=int) # noise level
    parser.add_argument('--use_TTA', default=True, type=bool, help="True/False")
    args = parser.parse_args()
    args.dims = [[240,240,160], [240,240,160]]
    args.modes = len(args.dims)

    train_list = os.path.join(args.root, args.train_dir, args.train_file)
    train_root = os.path.join(args.root, args.train_dir)
    train_set = BraTS(train_list, train_root, args.mode,args.input_dims)
    train_loader = DataLoader(dataset=train_set, batch_size=args.batch_size)
    print('Samples for train = {}'.format(len(train_set)))
    valid_list = os.path.join(args.root, args.valid_dir, args.valid_file)
    valid_root = os.path.join(args.root, args.valid_dir)
    valid_set = BraTS(valid_list, valid_root,'valid',args.input_dims)
    valid_loader = DataLoader(valid_set, batch_size=1)
    print('Samples for valid = {}'.format(len(valid_set)))
    test_list = os.path.join(args.root, args.test_dir, args.test_file)
    test_root = os.path.join(args.root, args.test_dir)
    test_set = BraTS(test_list, test_root,'test',args.input_dims)
    test_loader = DataLoader(test_set, batch_size=1)
    print('Samples for test = {}'.format(len(test_set)))

    model = TMSU(args.classes, args.modes, args.model_name, args.input_dims,args.epochs, args.lambda_epochs) # lambda KL divergence
    # Define the path to the saved file
    saved_file_path = '/kaggle/input/model-state-saved/current_epoch.pth'

    # Load the saved file into memory
    saved_data = torch.load(saved_file_path)

    # Extract the desired state_dict by its key
    state_dict_key = 'state_dict'
    model_state_dict = saved_data[state_dict_key]
    model.load_state_dict(model_state_dict)
    total = sum([param.nelement() for param in model.parameters()])
    print("Number of model's parameter: %.2fM" % (total / 1e6))
    optimizer = optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-5)


    model


    def train(epoch):

        model.train()
        loss_meter = AverageMeter()
        dice_total, iou_total = 0, 0
        whole_dice , core_dice, enhanching_dice = 0, 0, 0
        whole_iou , core_iou, enhanching_iou = 0, 0, 0
        step = 0
        dt_size = len(train_loader.dataset)
        for i, data in enumerate(train_loader):
            step += 1
            input, target = data
            x = input  # for multi-modal combine train
            x=x.to(device)
            target = target.to(device)
            args.mode = 'train'
            evidences, loss = model(x,target,epoch,args.mode)

            # max
            _, predicted = torch.max(evidences.data, 1)
            output = predicted

            target = torch.squeeze(target)
            iou_res = softmax_mIOU_score(output, target[:, :, :155])
            dice_res = softmax_output_dice(output, target[:, :, :155])
            dice_values = [t for t in dice_res]
            whole_dice += dice_values[0]
            core_dice += dice_values[1]
            enhanching_dice += dice_values[2]
            iou_values = [t for t in iou_res]
            whole_iou += iou_values[0]
            core_iou += iou_values[1]
            enhanching_iou += iou_values[2]
            dice_total += dice_res[1]
            iou_total += iou_res[1]
            # loss & noised loss
            
            print("%d/%d,train_loss:%0.3f" % (step, (dt_size - 1) // train_loader.batch_size + 1, loss))
            print('current_iou:{} ; current_dice:{}'.format(iou_res, dice_res))

            optimizer.zero_grad()
            loss.requires_grad_(True).backward()
            optimizer.step()

            loss_meter.update(loss)
        aver_dice = dice_total / len(train_loader)
        aver_iou = iou_total / len(train_loader)
        avg_whole_dice = whole_dice / len(train_loader)
        avg_core_dice = core_dice / len(train_loader)
        avg_enhancing_dice = enhanching_dice / len(train_loader)
        avg_whole_iou = whole_iou / len(train_loader)
        avg_core_iou = core_iou / len(train_loader)
        avg_enhancing_iou = enhanching_iou / len(train_loader)
        return loss_meter.avg, aver_dice, aver_iou, avg_whole_dice, avg_core_dice, avg_enhancing_dice, avg_whole_iou, avg_core_iou, avg_enhancing_iou

    def val(args,current_epoch,best_dice):
        print('===========>Validation begining!===========')
        model.eval()
        loss_meter = AverageMeter()
        dice_total, iou_total = 0, 0
        whole_dice , core_dice, enhanching_dice = 0, 0, 0
        whole_iou , core_iou, enhanching_iou = 0, 0, 0
        step = 0
        # model.eval()
        for i, data in enumerate(valid_loader):
            step += 1
            input, target = data
            x = input
            x=x.to(device)

            # add gaussian noise to input data
            # x = dict()
            # for m_num in range(input.shape[1]):
            #     x[m_num] = input[..., m_num, :, :, :, ].unsqueeze(1)
            target = target.to(device)

            with torch.no_grad():
                args.mode = 'val'
                evidences, loss = model(x, target[:, :, :, :155], current_epoch,args.mode) # two modality or four modality
                # max
                _, predicted = torch.max(evidences.data, 1)
                output = predicted

                target = torch.squeeze(target)
                iou_res = softmax_mIOU_score(output, target[:, :, :155])
                dice_res = softmax_output_dice(output, target[:, :, :155])
                print('current_iou:{} ; current_dice:{}'.format(iou_res, dice_res))
                dice_values = [t for t in dice_res]
                whole_dice += dice_values[0]
                core_dice += dice_values[1]
                enhanching_dice += dice_values[2]
                iou_values = [t for t in iou_res]
                whole_iou += iou_values[0]
                core_iou += iou_values[1]
                enhanching_iou += iou_values[2]
                dice_total += dice_res[1]
                iou_total += iou_res[1]
                # loss & noised loss
                loss_meter.update(loss)
        aver_dice = dice_total / len(valid_loader)
        aver_iou = iou_total / len(valid_loader)
        if aver_dice > best_dice \
                or (current_epoch + 1) % int(args.epochs - 1) == 0 \
                or (current_epoch + 1) % int(args.epochs - 2) == 0 \
                or (current_epoch + 1) % int(args.epochs - 3) == 0:
                print('aver_dice:{} > best_dice:{}'.format(aver_dice, best_dice))
                best_dice = aver_dice
                print('===========>save best model!')
                file_name = os.path.join('/kaggle/working/TBraTSwithoutDirichlet', '_epoch_{}.pth'.format(current_epoch))
                torch.save({
                    'epoch': current_epoch,
                    'state_dict': model.state_dict(),
                },
                    file_name)
        # print('====> aver_dice: {:.4f}'.format(aver_dice))
        # print('====> aver_iou: {:.4f}'.format(aver_iou))
        aver_dice = dice_total / len(train_loader)
        aver_iou = iou_total / len(train_loader)
        avg_whole_dice = whole_dice / len(train_loader)
        avg_core_dice = core_dice / len(train_loader)
        avg_enhancing_dice = enhanching_dice / len(train_loader)
        avg_whole_iou = whole_iou / len(train_loader)
        avg_core_iou = core_iou / len(train_loader)
        avg_enhancing_iou = enhanching_iou / len(train_loader)
        print('===========>save current model!')
        file_name = os.path.join('/kaggle/working/TBraTSwithoutDirichlet', 'current_epoch.pth')
        torch.save({
            'epoch': current_epoch,
            'state_dict': model.state_dict(),
        },
            file_name)
        return loss_meter.avg, best_dice, aver_dice, aver_iou, avg_whole_dice, avg_core_dice, avg_enhancing_dice, avg_whole_iou, avg_core_iou, avg_enhancing_iou

    def test(args):
        print('===========>Test begining!===========')

        loss_meter = AverageMeter()
        noised_loss_meter = AverageMeter()
        dice_total,iou_total = 0,0
        noised_dice_total,noised_iou_total = 0,0
        step = 0
        dt_size = len(test_loader.dataset)
        load_file = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                args.save_dir,
                                 '_epoch_{}.pth'.format(args.test_epoch))

        if os.path.exists(load_file):
            checkpoint = torch.load(load_file)
            model.load_state_dict(checkpoint['state_dict'])
            args.start_epoch = checkpoint['epoch']
            print('Successfully load checkpoint {}'.format(os.path.join(args.save_dir + '/_epoch_' + str(args.test_epoch))))
        else:
            print('There is no resume file to load!')
        model.eval()
        for i, data in enumerate(test_loader):
            step += 1
            input, target = data
            input=input.to(device)
            target = target.to(device)
            # add gaussian noise to input data
            # noise_m = torch.randn_like(input) * args.Variance
            # noised_input = input + noise_m
            # x = input.cuda()
            # noised_x = noised_input.cuda()
            x = dict()
            # noised_x = dict()
            for m_num in range(input.shape[1]):
                x[m_num] = input[...,m_num,:,:,:,].unsqueeze(1)
                # noised_x[m_num] = noised_input[...,m_num,:,:,:,].unsqueeze(1)
            target = target

            with torch.no_grad():
                args.mode = 'test'
                if not args.use_TTA:
                    evidences, loss = model(x, target[:, :, :, :155], args.epochs,args.mode)
                    # noised_evidences, noised_loss = model(noised_x, target[:, :, :, :155], args.epochs,args.mode)
                else:
                    evidences, loss = model(x, target[:, :, :, :155], args.epochs,args.mode,args.use_TTA)
                    # noised_evidences, noised_loss = model(noised_x, target[:, :, :, :155], args.epochs,args.mode,args.use_TTA)
                # results with TTA or not

                output = F.softmax(evidences, dim=1)
                # for input noise
                # noised_output = F.softmax(noised_evidences, dim=1)

                # dice
                output = output[0, :, :args.input_H, :args.input_W, :args.input_D]
                output = output.argmax(0)
                target = torch.squeeze(target)
                iou_res = softmax_mIOU_score(output, target[:, :, :155])
                dice_res = softmax_output_dice(output, target[:, :, :155])
                dice_total += dice_res[1]
                iou_total += iou_res[1]
                # for noise_x
                noised_output = noised_output[0, :, :args.input_H, :args.input_W, :args.input_D]
                noised_output = noised_output.argmax(0)
                noised_iou_res = softmax_mIOU_score(noised_output, target[:, :, :155])
                noised_dice_res = softmax_output_dice(noised_output, target[:, :, :155])
                noised_dice_total += noised_dice_res[1]
                noised_iou_total += noised_iou_res[1]
                print('current_dice:{} ; current_noised_dice:{}'.format(dice_res, noised_dice_res))
                # loss & noised loss
                loss_meter.update(loss)
                # noised_loss_meter.update(noised_loss)
        # noised_aver_dice = noised_dice_total / len(test_loader)
        aver_dice = dice_total / len(test_loader)
        # print('====> noised_aver_dice: {:.4f}'.format(noised_aver_dice))
        print('====> aver_dice: {:.4f}'.format(aver_dice))
        return loss_meter.avg,noised_loss_meter.avg, aver_dice


    epoch_loss = 0
    best_dice = 0
    training_losses = []
    validation_losses = []
    #tensorboard 
    tboardWriter=SummaryWriter('runs/simpleMultiClassification')
    try:
        for epoch in range(101, args.epochs + 1):
            start = time.time()
            print('===========Train begining!===========')
            print('Epoch {}/{}'.format(epoch, args.epochs - 1))
            epoch_loss, avg_epoch_dice, avg_epoch_iou, avg_whole_dice, avg_core_dice, avg_enhancing_dice, avg_whole_iou, avg_core_iou, avg_enhancing_iou = train(epoch)
            print("epoch %d avg_train_loss:%0.3f" % (epoch, epoch_loss))
            print("epoch %d avg_dice:%0.3f" % (epoch, avg_epoch_dice))
            print("epoch %d avg_iou:%0.3f" % (epoch, avg_epoch_iou))
            print("epoch %d avg_whole_tumor_dice_score:%0.3f" % (epoch, avg_whole_dice))
            print("epoch %d avg_tumor_core_dice_score:%0.3f" % (epoch, avg_core_dice))
            print("epoch %d avg_enhancing_tumor_dice_score:%0.3f" % (epoch, avg_enhancing_dice))
            print("epoch %d avg_whole_tumor_iou_score:%0.3f" % (epoch, avg_whole_iou))
            print("epoch %d avg_tumor_core_iou_score:%0.3f" % (epoch, avg_core_iou))
            print("epoch %d avg_enhancing_tumor_iou_score:%0.3f" % (epoch, avg_enhancing_iou))
            val_loss, best_dice, avg_epoch_dice, avg_epoch_iou, avg_whole_dice, avg_core_dice, avg_enhancing_dice, avg_whole_iou, avg_core_iou, avg_enhancing_iou = val(args,epoch,best_dice)
            print("epoch %d avg_val_loss:%0.3f" % (epoch, val_loss))
            print("epoch %d avg_dice:%0.3f" % (epoch, avg_epoch_dice))
            print("epoch %d avg_iou:%0.3f" % (epoch, avg_epoch_iou))
            print("epoch %d avg_whole_tumor_dice_score:%0.3f" % (epoch, avg_whole_dice))
            print("epoch %d avg_tumor_core_dice_score:%0.3f" % (epoch, avg_core_dice))
            print("epoch %d avg_enhancing_tumor_dice_score:%0.3f" % (epoch, avg_enhancing_dice))
            print("epoch %d avg_whole_tumor_iou_score:%0.3f" % (epoch, avg_whole_iou))
            print("epoch %d avg_tumor_core_iou_score:%0.3f" % (epoch, avg_core_iou))
            print("epoch %d avg_enhancing_tumor_iou_score:%0.3f" % (epoch, avg_enhancing_iou))
            training_losses.append(epoch_loss.detach().cpu().numpy())
            validation_losses.append(val_loss.detach().cpu().numpy())
            if epoch % 1 == 0:
                plt.plot(training_losses, label='Training loss')
                plt.plot(validation_losses, label='Validation loss')
                plt.xlabel('Epoch')
                plt.ylabel('Loss')
                plt.legend()
                plt.show()
                print("Training losses: ", training_losses)
                print("Validation losses: ",validation_losses)
            end = time.time()
            print("Time for epoch: ",end-start)
            tboardWriter.add_scalar("Loss/train", training_losses[epoch-101], epoch-101)
            tboardWriter.add_scalar("Loss/val", validation_losses[epoch-101], epoch-101)
    except KeyboardInterrupt:
        plt.plot(training_losses, label='Training loss')
        plt.plot(validation_losses, label='Validation loss')
        plt.xlabel('Epoch')
        plt.ylabel('Loss')
        plt.legend()
        plt.show()
    plt.plot(training_losses, label='Training loss')
    plt.plot(validation_losses, label='Validation loss')
    plt.xlabel('Epoch')
    plt.ylabel('Loss')
    plt.legend()
    plt.show()
    print("Training losses: ", training_losses)
    print("Validation losses: ",validation_losses)
    test_loss,noised_test_loss, test_dice,noised_test_dice = test(args)
