import chainer.functions as F
from chainer import Variable, FunctionSet, optimizers
from chainer.links import caffe
from chainer import computational_graph
from chainer import cuda
from chainer import optimizers
from chainer import serializers
from tensor import *
from network import *
from deel import *
import json
import os
import multiprocessing
import threading
import time
import six
import numpy as np
import os.path
from PIL import Image
#import six.moves.cPickle as pickle
from six.moves import queue
import pickle
import cv2
import hashlib
import datetime
import sys
import random



"""Input something to context tensor"""
def Input(x):
	if isinstance(x,str):
		root, ext = os.path.splitext(x)
		if ext=='.png' or ext=='.jpg' or ext=='.jpeg' or ext=='.gif':
			img = Image.open(x)
			t = ImageTensor(img)
			t.use()
		elif ext=='.txt':
			print "this is txt"

	return t


'''
	Trainer
'''
class BatchTrainer(object):
	batchsize=32
	val_batchsize=250
	data_q=None
	res_q=None
	loaderjob=20
	train_list=''
	val_list=''
	def __init__(self,in_size=256):
		BatchTrainer.data_q = queue.Queue(maxsize=1)
		BatchTrainer.res_q = queue.Queue()
		BatchTrainer.in_size=ImageNet.in_size
	def train(self,workout,optimizer=None):
		BatchTrainer.train_list = load(Deel.train,Deel.root)
		BatchTrainer.val_list = load(Deel.val,Deel.root)


		feeder = threading.Thread(target=feed_data)
		feeder.daemon = True
		feeder.start()
		logger = threading.Thread(target=log_result)
		logger.daemon = True
		logger.start()	

		BatchTrainer.workout = workout

		train_loop()
		feeder.join()
		logger.join()

optimizer_lr=0.1

def load(path, root):
	tuples = []
	for line in open(path):
		pair = line.strip().split()
		tuples.append((os.path.join(root, pair[0]), np.int32(pair[1])))
	return tuples


def read_image(path, center=False, flip=False):
	cropwidth = 256 - ImageNet.in_size
	image = np.asarray(Image.open(path)).transpose(2, 0, 1)
	if center:
		top = left = cropwidth / 2
	else:
		top = random.randint(0, cropwidth - 1)
		left = random.randint(0, cropwidth - 1)
	bottom = ImageNet.in_size + top
	right = ImageNet.in_size + left

	image = image[:, top:bottom, left:right].astype(np.float32)
	image -= ImageNet.mean_image[:, top:bottom, left:right]
	image /= 255
	if flip and random.randint(0, 1) == 0:
		return image[:, :, ::-1]
	else:
		return image

def feed_data():
	global optimizer_lr
	# Data feeder
	i = 0
	count = 0
	in_size = BatchTrainer.in_size
	batchsize = BatchTrainer.batchsize
	val_batchsize = BatchTrainer.val_batchsize
	train_list = BatchTrainer.train_list

	x_batch = np.ndarray(
		(batchsize, 3, in_size, in_size), dtype=np.float32)
	y_batch = np.ndarray((batchsize,), dtype=np.int32)
	val_x_batch = np.ndarray(
		(val_batchsize, 3, in_size, in_size), dtype=np.float32)
	val_y_batch = np.ndarray((val_batchsize,), dtype=np.int32)

	batch_pool = [None] * batchsize
	val_batch_pool = [None] * val_batchsize
	pool = multiprocessing.Pool(BatchTrainer.loaderjob)
	BatchTrainer.data_q.put('train')
	for epoch in six.moves.range(1, 1 + Deel.epoch):
		print('epoch', epoch)
		
		perm = np.random.permutation(len(train_list))
		for idx in perm:
			path, label = train_list[idx]
			batch_pool[i] = pool.apply_async(read_image, (path, False, True))
			y_batch[i] = label
			i += 1

			if i == BatchTrainer.batchsize:

				for j, x in enumerate(batch_pool):
					x_batch[j] = x.get()
				BatchTrainer.data_q.put((x_batch.copy(), y_batch.copy()))
				i = 0

			count += 1
			if count % 100000 == 0:
				BatchTrainer.data_q.put('val')
				j = 0
				for path, label in val_list:
					val_batch_pool[j] = pool.apply_async(
						read_image, (path, True, False))
					val_y_batch[j] = label
					j += 1

					if j == args.val_batchsize:
						for k, x in enumerate(val_batch_pool):
							val_x_batch[k] = x.get()
						BatchTrainer.data_q.put((val_x_batch.copy(), val_y_batch.copy()))
						j = 0
				BatchTrainer.data_q.put('train')
		Deel.optimizer_lr *= 0.97

	pool.close()
	pool.join()
	BatchTrainer.data_q.put('end')

def log_result():
	# Logger
	train_count = 0
	train_cur_loss = 0
	train_cur_accuracy = 0
	begin_at = time.time()
	val_begin_at = None
	while True:
		result = BatchTrainer.res_q.get()
		if result == 'end':
			break
		elif result == 'train':
			train = True
			if val_begin_at is not None:
				begin_at += time.time() - val_begin_at
				val_begin_at = None
			continue
		elif result == 'val':
			train = False
			val_count = val_loss = val_accuracy = 0
			val_begin_at = time.time()
			continue

		loss, accuracy = result
		if train:
			train_count += 1
			duration = time.time() - begin_at
			throughput = train_count * BatchTrainer.batchsize / duration
			print(
				'\rtrain {} updates ({} samples) time: {} ({} images/sec)'
				.format(train_count, train_count * BatchTrainer.batchsize,
						datetime.timedelta(seconds=duration), throughput))

			train_cur_loss += loss
			train_cur_accuracy += accuracy
			if train_count % 1000 == 0:
				mean_loss = train_cur_loss / 1000
				mean_error = 1 - train_cur_accuracy / 1000
				print(json.dumps({'type': 'train', 'iteration': train_count,
								  'error': mean_error, 'loss': mean_loss}))
				sys.stdout.flush()
				train_cur_loss = 0
				train_cur_accuracy = 0
		else:
			val_count += args.val_batchsize
			duration = time.time() - val_begin_at
			throughput = val_count / duration
			print(
				'\rval   {} batches ({} samples) time: {} ({} images/sec)'
				.format(val_count / args.val_batchsize, val_count,
						datetime.timedelta(seconds=duration), throughput))

			val_loss += loss
			val_accuracy += accuracy
			if val_count == 50000:
				mean_loss = val_loss * args.val_batchsize / 50000
				mean_error = 1 - val_accuracy * args.val_batchsize / 50000
				print(json.dumps({'type': 'val', 'iteration': train_count,
								  'error': mean_error, 'loss': mean_loss}))


def train_loop():
	global workout
	train=True
	while True:
		while BatchTrainer.data_q.empty():
			time.sleep(0.1)
		inp = BatchTrainer.data_q.get()
		if inp == 'end':  # quit
			BatchTrainer.res_q.put('end')
			break
		elif inp == 'train':  # restart training
			BatchTrainer.res_q.put('train')
			train=True
			continue
		elif inp == 'val':  # start validation
			BatchTrainer.res_q.put('val')
			train=False
			continue

		volatile = 'off' if train else 'on'

		x = ChainerTensor(Variable(xp.asarray(inp[0]), volatile=volatile))
		t = ChainerTensor(Variable(xp.asarray(inp[1]), volatile=volatile))

		result = workout(x,t)
		loss = result.content.loss
		accuracy = result.content.accuracy

		BatchTrainer.res_q.put((float(loss.data), float(accuracy.data)))
		del x, t




def InputBatch(train='data/train.txt',val='data/test.txt'):
	Deel.train=train
	Deel.val = val



def BatchTrain(callback):
	global workout
	trainer = BatchTrainer()
#	trainer.train(workout)

	BatchTrainer.train_list = load(Deel.train,Deel.root)
	BatchTrainer.val_list = load(Deel.val,Deel.root)


	feeder = threading.Thread(target=feed_data)
	feeder.daemon = True
	feeder.start()
	logger = threading.Thread(target=log_result)
	logger.daemon = True
	logger.start()	

	workout = callback

	train_loop()
	feeder.join()
	logger.join()



def Show(x=None):
	if x==None:
		x = Tensor.context

	x.show()

def ShowLabels(x=None):
	if x==None:
		x = Tensor.context

	t = LabelTensor(x)

	t.show()


def Output(x=None,num_of_candidate=5):
	if x==None:
		x = Tensor.context

	out = x.output

	out.sort(cmp=lambda a, b: cmp(a[0], b[0]), reverse=True)
	for rank, (score, name) in enumerate(out[:num_of_candidate], start=1):
		print('#%d | %s | %4.1f%%' % (rank, name, score * 100))


