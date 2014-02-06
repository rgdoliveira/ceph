#!/usr/bin/env python
import contextlib
import logging
from cStringIO import StringIO

from ..orchestra import run
from teuthology import misc

"""
https://github.com/openstack-dev/devstack/blob/master/README.md
http://ceph.com/docs/master/rbd/rbd-openstack/
"""
log = logging.getLogger(__name__)

DEVSTACK_GIT_REPO = 'https://github.com/openstack-dev/devstack.git'


@contextlib.contextmanager
def task(ctx, config):
    """
    Install OpenStack DevStack and configure it to use a Ceph cluster for
    Glance and Cinder.

    Requires one node with a role 'devstack'
    """
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise TypeError("config must be a dict")

    # SETUP
    is_devstack_node = lambda role: role.startswith('devstack')
    is_osd_node = lambda role: role.startswith('osd')
    devstack_node = ctx.cluster.only(is_devstack_node).remotes.keys()[0]
    an_osd_node = ctx.cluster.only(is_osd_node).remotes.keys()[0]
    install_devstack(devstack_node)
    try:
        # OTHER STUFF
        configure_devstack_and_ceph(ctx, devstack_node, an_osd_node)
        yield
    #except Exception as e:
        # FAIL
        #pass
    finally:
        # CLEANUP
        pass


def install_devstack(devstack_node):
    log.info("Cloning devstack repo...")
    args = ['git', 'clone', DEVSTACK_GIT_REPO]
    devstack_node.run(args=args)

    log.info("Installing devstack...")
    args = ['cd', 'devstack', run.Raw('&&'), './stack.sh']
    devstack_node.run(args=args)


def configure_devstack_and_ceph(config, devstack_node, ceph_node):
    # Create pools on Ceph cluster
    pool_size = config.get('pool_size', 128)
    for pool_name in ['volumes', 'images', 'backups']:
        args = ['ceph', 'osd', 'pool', 'create', pool_name, pool_size]
        ceph_node.run(args=args)

    # Copy ceph.conf to OpenStack node
    misc.copy_file(ceph_node, '/etc/ceph/ceph.conf', devstack_node)
    # This is where we would install python-ceph and ceph-common but it
    # appears the ceph task will do that for us.
    ceph_auth_cmds = [
        ['ceph', 'auth', 'get-or-create', 'client.cinder', 'mon',
            'allow r', 'osd', 'allow class-read object_prefix rbd_children, allow rwx pool=volumes, allow rx pool=images'],  # noqa
        ['ceph', 'auth', 'get-or-create', 'client.glance', 'mon',
            'allow r', 'osd', 'allow class-read object_prefix rbd_children, allow rwx pool=images'],  # noqa
        ['ceph', 'auth', 'get-or-create', 'client.cinder-backup', 'mon',
            'allow r', 'osd', 'allow class-read object_prefix rbd_children, allow rwx pool=backups'],  # noqa
    ]
    for cmd in ceph_auth_cmds:
        ceph_node.run(args=cmd)

    # Copy ceph auth keys to devstack node
    def copy_key(from_remote, key_name, to_remote, dest_path, owner):
        key_stringio = StringIO()
        from_remote.run(
            args=['ceph', 'auth', 'get-or-create', key_name],
            stdout=key_stringio)
        misc.sudo_write_file(to_remote, dest_path,
                             key_stringio, owner=owner)
    keys = [
        dict(name='client.glance',
             path='/etc/ceph/ceph.client.glance.keyring',
             owner='glance:glance'),
        dict(name='client.cinder',
             path='/etc/ceph/ceph.client.cinder.keyring',
             owner='cinder:cinder'),
        dict(name='client.cinder-backup',
             path='/etc/ceph/ceph.client.cinder-backup.keyring',
             owner='cinder:cinder'),
    ]
    for key_dict in keys:
        copy_key(ceph_node, key_dict['name'], devstack_node,
                 key_dict['path'], key_dict['owner'])
