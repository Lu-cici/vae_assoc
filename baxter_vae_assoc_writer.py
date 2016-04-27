import itertools
import cPickle as cp

import numpy as np
import tensorflow as tf

import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

import rospy
from sensor_msgs.msg import JointState
from std_msgs.msg import Empty

import baxter_writer as bw

import dataset
import vae_assoc

import utils

class BaxterVAEAssocWriter(bw.BaxterWriter):

    def __init__(self):
        bw.BaxterWriter.__init__(self)

        self.vae_assoc_model = None
        self.initialize_tf_environment()

        self.initialize_dataset()
        return

    def initialize_tf_environment(self):
        self.batch_size = 100
        self.n_z = 5
        self.assoc_lambda = 15

        self.img_network_architecture = \
            dict(scope='image',
                 n_hidden_recog_1=500, # 1st layer encoder neurons
                 n_hidden_recog_2=300, # 2nd layer encoder neurons
                 n_hidden_gener_1=300, # 1st layer decoder neurons
                 n_hidden_gener_2=500, # 2nd layer decoder neurons
                 n_input=784, # MNIST data input (img shape: 28*28)
                 n_z=self.n_z)  # dimensionality of latent space

        self.jnt_network_architecture = \
            dict(scope='joint',
                 n_hidden_recog_1=200, # 1st layer encoder neurons
                 n_hidden_recog_2=200, # 2nd layer encoder neurons
                 n_hidden_gener_1=200, # 1st layer decoder neurons
                 n_hidden_gener_2=200, # 2nd lAttempting to use uninitialized valueayer decoder neurons
                 n_input=147, # 21 bases for each function approximator
                 n_z=self.n_z)  # dimensionality of latent space

        self.initialize_vae_assoc()
        return

    def initialize_vae_assoc(self):
        #close the session of the existing model
        # if self.vae_assoc_model is not None:
        #     self.vae_assoc_model.sess.close()
        self.vae_assoc_model = vae_assoc.AssocVariationalAutoEncoder([self.img_network_architecture, self.jnt_network_architecture],
                                     [True, False],
                                     transfer_fct=tf.nn.relu,
                                     assoc_lambda=self.assoc_lambda,
                                     learning_rate=0.0001,
                                     batch_size=self.batch_size)
        return

    def initialize_dataset(self):
        img_data = utils.extract_images(fname='bin/img_data.pkl', only_digits=False)
        #we need mean and standard deviation to restore the function approximator
        fa_data, self.fa_mean, self.fa_std = utils.extract_jnt_fa_parms(fname='bin/jnt_fa_data.pkl', only_digits=False)

        fa_data_normed = (fa_data - self.fa_mean) / self.fa_std

        # fa_data_sets = dataset.construct_datasets(fa_data_normed)

        #put them together
        aug_data = np.concatenate((img_data, fa_data_normed), axis=1)

        self.data_sets = dataset.construct_datasets(aug_data)
        return

    def train_model(self):
        # if self.vae_assoc_model is not None:
        #     self.vae_assoc_model.sess.close()
        tf.reset_default_graph()
        self.vae_assoc_model, self.cost_hist = vae_assoc.train(self.data_sets, [self.img_network_architecture, self.jnt_network_architecture], binary=[True, False], assoc_lambda = self.assoc_lambda, learning_rate=0.0001,
                    batch_size=self.batch_size, training_epochs=5000, display_step=5)
        return

    def save_model(self):
        if self.vae_assoc_model is not None:
            self.vae_assoc_model.save_model('output/model_batchsize{}_nz{}_lambda{}.ckpt'.format(self.batch_size, self.n_z, self.assoc_lambda))
        return

    def load_model(self, folder=None, fname=None):
        #prepare network and load from a file
        tf.reset_default_graph()
        self.initialize_vae_assoc()
        self.vae_assoc_model.restore_model(folder, fname)
        return

    def derive_robot_motion_from_from_img(self, img):
        if self.vae_assoc_model is not None:
            if len(img.shape) == 1:
                assert len(img) == 784
                #construct fake data to pad the batch
                input_img_data = np.random.rand(self.batch_size, 784) - 0.5
                input_img_data[0] = img
            else:
                assert img.shape[0] == self.batch_size
                assert img.shape[1] == 784

                input_img_data = img

            #construct input with fake joint parms
            X = [input_img_data, np.random.rand(self.batch_size, 147) - 0.5]

            #use recognition model to infer the latent representation
            z_rep = self.vae_assoc_model.transform(X)
            #now remember to only use the z_rep of img to
            #generate the joint output
            x_reconstr_means = self.vae_assoc_model.generate(z_mu=z_rep[0])

            if len(img.shape) == 1:
                #take the first joint fa param and restore it for the evaluation
                fa_parms = (x_reconstr_means[1] * self.fa_std + self.fa_mean)[0]
                jnt_motion = np.array(self.derive_jnt_traj_from_fa_parms(np.reshape(fa_parms, (7, -1))))
                cart_motion = np.array(self.derive_cartesian_trajectory_from_fa_parms(np.reshape(fa_parms, (7, -1))))
            else:
                #take all the samples if the input is a batch
                fa_parms = (x_reconstr_means[1] * self.fa_std + self.fa_mean)
                jnt_motion = [np.array(self.derive_jnt_traj_from_fa_parms(np.reshape(fa, (7, -1)))) for fa in fa_parms]
                cart_motion = [np.array(self.derive_cartesian_trajectory_from_fa_parms(np.reshape(fa, (7, -1)))) for fa in fa_parms]

            return jnt_motion, cart_motion

        return None, None

import os

def main():
    #prepare a writer and load a trained model
    tf.reset_default_graph()
    bvaw = BaxterVAEAssocWriter()

    curr_dir = os.path.dirname(os.path.realpath(__file__))

    bvaw.load_model(os.path.join(curr_dir, 'output/large_lambda'), 'model_batchsize100_nz5_lambda15.ckpt')
    print 'Number of variabels:', len(tf.all_variables())
    n_test = 20

    #prepare ros stuff
    rospy.init_node('baxter_vaeassoc_writer')
    r = rospy.Rate(100)

    jnt_pub = rospy.Publisher('/baxter_openrave_writer/joint_cmd', JointState, queue_size=10)
    cln_pub = rospy.Publisher('/baxter_openrave_writer/clear_cmd', Empty, queue_size=10)

    plt.ion()

    test_sample = bvaw.data_sets.test.next_batch(bvaw.batch_size)[0] #the first is feature, the second is the label
    test_img_sample = test_sample[:, :784]
    jnt_motion, cart_motion = bvaw.derive_robot_motion_from_from_img(test_img_sample)

    raw_input('ENTER to start the test...')

    for i in range(n_test):
        #prepare image to show
        fig = plt.figure()
        ax_img = fig.add_subplot(121)
        ax_img.imshow(test_img_sample[i].reshape(28, 28), vmin=0, vmax=1, cmap='gray')
        ax_img.set_title("Test Image Input")
        # plt.colorbar()

        ax_cart = fig.add_subplot(122)
        ax_cart.plot(cart_motion[i][:, 0], -cart_motion[i][:, 1], linewidth=3.5)
        ax_cart.set_title("Associative Motion")
        ax_cart.set_aspect('equal')

        # print 'z coord mean and std: {}, {}'.format(z_coord_mean, z_coord_std)

        plt.draw()

        print 'Sending joint command to a viewer...'
        cln_pub.publish(Empty())
        for k in range(10):
            r.sleep()
        jnt_msg = JointState()
        for cmd in jnt_motion[i]:
            jnt_msg.position = cmd
            jnt_pub.publish(jnt_msg)
            r.sleep()

        raw_input()
    return

if __name__ == '__main__':
    np.random.seed(0)
    tf.set_random_seed(0)
    main()