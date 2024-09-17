"""
pre-training funcs     Script  ver： Feb 8th 16:00
有修改loss backward

"""
import builtins
import datetime
import os
import time
from collections import defaultdict, deque
from pathlib import Path

import torch
import torch.distributed as dist

try:
    from torch import inf
except:
    from torch._six import inf


# SmoothedValue operator
class SmoothedValue(object):
    """Track a series of values and provide access to smoothed values over a
    window or the global series average.
    """

    def __init__(self, window_size=20, fmt=None):
        if fmt is None:
            fmt = "{median:.4f} ({global_avg:.4f})"
        self.deque = deque(maxlen=window_size)
        self.total = 0.0
        self.count = 0
        self.fmt = fmt

    def update(self, value, n=1):
        self.deque.append(value)
        self.count += n
        self.total += value * n

    def synchronize_between_processes(self):
        """
        Warning: does not synchronize the deque!
        """
        if not is_dist_avail_and_initialized():
            return
        t = torch.tensor([self.count, self.total], dtype=torch.float64, device='cuda')
        dist.barrier()
        dist.all_reduce(t)
        t = t.tolist()
        self.count = int(t[0])
        self.total = t[1]

    @property
    def median(self):
        d = torch.tensor(list(self.deque))
        return d.median().item()

    @property
    def avg(self):
        d = torch.tensor(list(self.deque), dtype=torch.float32)
        return d.mean().item()

    @property
    def global_avg(self):
        return self.total / self.count

    @property
    def max(self):
        return max(self.deque)

    @property
    def value(self):
        return self.deque[-1]

    def __str__(self):
        return self.fmt.format(
            median=self.median,
            avg=self.avg,
            global_avg=self.global_avg,
            max=self.max,
            value=self.value)


class MetricLogger(object):
    def __init__(self, delimiter="\t"):
        self.meters = defaultdict(SmoothedValue)  # SmoothedValue operator
        self.delimiter = delimiter

    def update(self, **kwargs):  # 更新内容字典
        for k, v in kwargs.items():
            if v is None:
                continue
            if isinstance(v, torch.Tensor):
                v = v.item()
            assert isinstance(v, (float, int))
            self.meters[k].update(v)

    def __getattr__(self, attr):  # 报错
        if attr in self.meters:
            return self.meters[attr]
        if attr in self.__dict__:
            return self.__dict__[attr]
        raise AttributeError("'{}' object has no attribute '{}'".format(
            type(self).__name__, attr))

    def __str__(self):  # 转换为str给print
        loss_str = []
        for name, meter in self.meters.items():
            loss_str.append(
                "{}: {}".format(name, str(meter))
            )
        return self.delimiter.join(loss_str)

    def synchronize_between_processes(self):  # 多进程同步
        for meter in self.meters.values():
            meter.synchronize_between_processes()

    def add_meter(self, name, meter):  # 新增一个indicator元素
        self.meters[name] = meter

    def log_every(self, iterable, print_freq, header=None):  # warp minibatch
        # 初始化迭代idx
        i = 0
        # 初始化头文件
        if not header:
            header = ''

        # 初始化计时
        start_time = time.time()
        end = time.time()
        iter_time = SmoothedValue(fmt='{avg:.4f}')
        data_time = SmoothedValue(fmt='{avg:.4f}')
        space_fmt = ':' + str(len(str(len(iterable)))) + 'd'
        # 初始化输出
        log_msg = [
            header,
            '[{0' + space_fmt + '}/{1}]',
            'eta: {eta}',
            '{meters}',
            'time: {time}',
            'data: {data}'
        ]
        if torch.cuda.is_available():
            log_msg.append('max mem: {memory:.0f}')

        log_msg = self.delimiter.join(log_msg)  # 缩进

        MB = 1024.0 * 1024.0

        for obj in iterable:

            data_time.update(time.time() - end)
            yield obj  # 生成迭代的下一个对象
            iter_time.update(time.time() - end)

            if i % print_freq == 0 or i == len(iterable) - 1:
                # 估算时间
                eta_seconds = iter_time.global_avg * (len(iterable) - i)
                eta_string = str(datetime.timedelta(seconds=int(eta_seconds)))
                # 输出
                if torch.cuda.is_available():
                    print(log_msg.format(
                        i, len(iterable), eta=eta_string,
                        meters=str(self),
                        time=str(iter_time), data=str(data_time),
                        memory=torch.cuda.max_memory_allocated() / MB))
                else:
                    print(log_msg.format(
                        i, len(iterable), eta=eta_string,
                        meters=str(self),
                        time=str(iter_time), data=str(data_time)))
            i += 1
            end = time.time()

        total_time = time.time() - start_time
        total_time_str = str(datetime.timedelta(seconds=int(total_time)))
        print('{} Total time: {} ({:.4f} s / it)'.format(
            header, total_time_str, total_time / len(iterable)))


def setup_for_distributed(is_master):
    """
    This function disables printing when not in master process
    """
    builtin_print = builtins.print

    def print(*args, **kwargs):
        force = kwargs.pop('force', False)
        force = force or (get_world_size() > 8)
        if is_master or force:
            now = datetime.datetime.now().time()
            builtin_print('[{}] '.format(now), end='')  # print with time stamp
            builtin_print(*args, **kwargs)

    builtins.print = print


def is_dist_avail_and_initialized():
    if not dist.is_available():
        return False
    if not dist.is_initialized():
        return False
    return True


def get_world_size():
    if not is_dist_avail_and_initialized():
        return 1
    return dist.get_world_size()


def get_rank():
    if not is_dist_avail_and_initialized():
        return 0
    return dist.get_rank()


def is_main_process():
    return get_rank() == 0


def save_on_master(*args, **kwargs):
    if is_main_process():
        torch.save(*args, **kwargs)


def init_distributed_mode(args):
    """
    配置多服务器环境文件信息，安排args.DDP_distributed

    :param args:
    :return:
    """
    if args.dist_on_itp:
        args.rank = int(os.environ['OMPI_COMM_WORLD_RANK'])
        args.world_size = int(os.environ['OMPI_COMM_WORLD_SIZE'])
        args.gpu = int(os.environ['OMPI_COMM_WORLD_LOCAL_RANK'])
        args.dist_url = "tcp://%s:%s" % (os.environ['MASTER_ADDR'], os.environ['MASTER_PORT'])
        os.environ['LOCAL_RANK'] = str(args.gpu)
        os.environ['RANK'] = str(args.rank)
        os.environ['WORLD_SIZE'] = str(args.world_size)
        # ["RANK", "WORLD_SIZE", "MASTER_ADDR", "MASTER_PORT", "LOCAL_RANK"]

    elif 'RANK' in os.environ and 'WORLD_SIZE' in os.environ:
        args.rank = int(os.environ["RANK"])
        args.world_size = int(os.environ['WORLD_SIZE'])
        args.gpu = int(os.environ['LOCAL_RANK'])

    elif 'SLURM_PROCID' in os.environ:
        args.rank = int(os.environ['SLURM_PROCID'])
        args.gpu = args.rank % torch.cuda.device_count()

    else:
        print('Not using DDP_distributed mode')
        setup_for_distributed(is_master=True)  # hack
        args.DDP_distributed = False
        return

    args.DDP_distributed = True

    torch.cuda.set_device(args.gpu)
    args.dist_backend = 'nccl'
    print('| DDP_distributed init (rank {}): {}, gpu {}'.format(
        args.rank, args.dist_url, args.gpu), flush=True)
    torch.distributed.init_process_group(backend=args.dist_backend, init_method=args.dist_url,
                                         world_size=args.world_size, rank=args.rank)
    torch.distributed.barrier()
    setup_for_distributed(args.rank == 0)


class NativeScalerWithGradNormCount:
    """
    定义的 loss 优化器

    基于自动混合精度训练设置的loss_scaler，额外增加了梯度裁剪的功能
    """
    state_dict_key = "amp_scaler"

    def __init__(self, GPU_count=1, DDP_distributed=False):
        self._scaler = torch.cuda.amp.GradScaler()
        self.GPU_count = GPU_count
        self.DDP_distributed=DDP_distributed

    def __call__(self, loss, optimizer, clip_grad=None, parameters=None, create_graph=False, update_grad=True):

        # 反传
        if self.DDP_distributed:
            loss = loss.unsqueeze(-1)
            self._scaler.scale(loss).backward(loss, create_graph=create_graph)  # create_graph
        else:
            if self.GPU_count == 1:  # only one GPU
                loss = loss.unsqueeze(-1)  # fixme 加了expand解决梯度标量问题，原本设计为了多卡，多卡有形状，单卡变没有形状的标量了
            # fixme 加了ones_like不知道为啥存在, 可能原本是分布式多个word
            self._scaler.scale(loss).backward(torch.ones_like(loss), create_graph=create_graph)  # create_graph

        if update_grad:
            # 梯度裁剪
            if clip_grad is not None:
                assert parameters is not None
                self._scaler.unscale_(optimizer)  # unscale the gradients of optimizer's assigned params in-place
                norm = torch.nn.utils.clip_grad_norm_(parameters, clip_grad)
            else:
                self._scaler.unscale_(optimizer)
                norm = get_grad_norm_(parameters)

            self._scaler.step(optimizer)  # 使用optimizer更新模型

            self._scaler.update()
        else:
            norm = None

        return norm

    def state_dict(self):  # 记录loss_scaler的state_dict，应该就是保存梯度
        return self._scaler.state_dict()

    def load_state_dict(self, state_dict):  # 还原某个checkpoint的state_dict
        self._scaler.load_state_dict(state_dict)


def get_grad_norm_(parameters, norm_type: float = 2.0) -> torch.Tensor:

    if isinstance(parameters, torch.Tensor):
        parameters = [parameters]

    # 确定需要梯度的模型参数
    parameters = [p for p in parameters if p.grad is not None]
    norm_type = float(norm_type)

    if len(parameters) == 0:
        return torch.tensor(0.)

    # 从对应GPU上进行操作
    device = parameters[0].grad.device

    if norm_type == inf:
        # 面对norm_type == inf爆炸值，保留
        total_norm = max(p.grad.detach().abs().max().to(device) for p in parameters)
    else:
        # 无norm_type == inf爆炸值，做norm
        total_norm = torch.norm(torch.stack([torch.norm(p.grad.detach(), norm_type).to(device) for p in parameters]), norm_type)

    return total_norm


def save_model(args, epoch, model, model_without_ddp, optimizer, loss_scaler, model_idx='SAE_'):
    output_dir = Path(args.output_dir)
    epoch_name = str(epoch)

    if loss_scaler is not None:
        checkpoint_paths = [output_dir / (model_idx+'_checkpoint-%s.pth' % epoch_name)]
        for checkpoint_path in checkpoint_paths:
            to_save = {
                'model': model_without_ddp.state_dict(),
                'optimizer': optimizer.state_dict(),
                'epoch': epoch,
                'scaler': loss_scaler.state_dict(),
                'args': args,  # 保存配置参数，但是在加载的时候不加载
            }

            save_on_master(to_save, checkpoint_path)
    else:
        client_state = {'epoch': epoch}
        model.save_checkpoint(save_dir=args.output_dir, tag="checkpoint-%s" % epoch_name, client_state=client_state)


def load_model(args, model_without_ddp, optimizer, loss_scaler):

    # 加载配置checkpoint的路径args.resume，默认没有则不加载
    if args.resume:
        if args.resume.startswith('https'):
            checkpoint = torch.hub.load_state_dict_from_url(
                args.resume, map_location='cpu', check_hash=True)
        else:
            checkpoint = torch.load(args.resume, map_location='cpu')

        model_without_ddp.load_state_dict(checkpoint['model'])

        print("Resume checkpoint %s" % args.resume)

        if 'optimizer' in checkpoint and 'epoch' in checkpoint and not (hasattr(args, 'eval') and args.eval):
            optimizer.load_state_dict(checkpoint['optimizer'])
            args.start_epoch = checkpoint['epoch'] + 1

            if 'scaler' in checkpoint:
                loss_scaler.load_state_dict(checkpoint['scaler'])

            print("With optim & sched!")


# 计算平均在单卡上的loss
def all_reduce_mean(x):
    world_size = get_world_size()

    if world_size > 1:
        x_reduce = torch.tensor(x).cuda()
        dist.all_reduce(x_reduce)
        x_reduce /= world_size
        return x_reduce.item()

    else:
        return x