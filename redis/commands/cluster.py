from redis.crc import key_slot
from redis.exceptions import RedisClusterException, RedisError

from .core import (
    ACLCommands,
    DataAccessCommands,
    FunctionCommands,
    ManagementCommands,
    PubSubCommands,
    ScriptCommands,
)
from .helpers import list_or_args
from .redismodules import RedisModuleCommands


class ClusterMultiKeyCommands:
    """
    A class containing commands that handle more than one key
    """

    def _partition_keys_by_slot(self, keys):
        """
        Split keys into a dictionary that maps a slot to
        a list of keys.
        """
        slots_to_keys = {}
        for key in keys:
            k = self.encoder.encode(key)
            slot = key_slot(k)
            slots_to_keys.setdefault(slot, []).append(key)

        return slots_to_keys

    def mget_nonatomic(self, keys, *args):
        """
        Splits the keys into different slots and then calls MGET
        for the keys of every slot. This operation will not be atomic
        if keys belong to more than one slot.

        Returns a list of values ordered identically to ``keys``
        """

        from redis.client import EMPTY_RESPONSE

        options = {}
        if not args:
            options[EMPTY_RESPONSE] = []

        # Concatenate all keys into a list
        keys = list_or_args(keys, args)
        # Split keys into slots
        slots_to_keys = self._partition_keys_by_slot(keys)

        # Call MGET for every slot and concatenate
        # the results
        # We must make sure that the keys are returned in order
        all_results = {}
        for slot_keys in slots_to_keys.values():
            slot_values = self.execute_command("MGET", *slot_keys, **options)

            slot_results = dict(zip(slot_keys, slot_values))
            all_results.update(slot_results)

        # Sort the results
        vals_in_order = [all_results[key] for key in keys]
        return vals_in_order

    def mset_nonatomic(self, mapping):
        """
        Sets key/values based on a mapping. Mapping is a dictionary of
        key/value pairs. Both keys and values should be strings or types that
        can be cast to a string via str().

        Splits the keys into different slots and then calls MSET
        for the keys of every slot. This operation will not be atomic
        if keys belong to more than one slot.
        """

        # Partition the keys by slot
        slots_to_pairs = {}
        for pair in mapping.items():
            # encode the key
            k = self.encoder.encode(pair[0])
            slot = key_slot(k)
            slots_to_pairs.setdefault(slot, []).extend(pair)

        # Call MSET for every slot and concatenate
        # the results (one result per slot)
        res = []
        for pairs in slots_to_pairs.values():
            res.append(self.execute_command("MSET", *pairs))

        return res

    def _split_command_across_slots(self, command, *keys):
        """
        Runs the given command once for the keys
        of each slot. Returns the sum of the return values.
        """
        # Partition the keys by slot
        slots_to_keys = self._partition_keys_by_slot(keys)

        # Sum up the reply from each command
        total = 0
        for slot_keys in slots_to_keys.values():
            total += self.execute_command(command, *slot_keys)

        return total

    def exists(self, *keys):
        """
        Returns the number of ``names`` that exist in the
        whole cluster. The keys are first split up into slots
        and then an EXISTS command is sent for every slot
        """
        return self._split_command_across_slots("EXISTS", *keys)

    def delete(self, *keys):
        """
        Deletes the given keys in the cluster.
        The keys are first split up into slots
        and then an DEL command is sent for every slot

        Non-existant keys are ignored.
        Returns the number of keys that were deleted.
        """
        return self._split_command_across_slots("DEL", *keys)

    def touch(self, *keys):
        """
        Updates the last access time of given keys across the
        cluster.

        The keys are first split up into slots
        and then an TOUCH command is sent for every slot

        Non-existant keys are ignored.
        Returns the number of keys that were touched.
        """
        return self._split_command_across_slots("TOUCH", *keys)

    def unlink(self, *keys):
        """
        Remove the specified keys in a different thread.

        The keys are first split up into slots
        and then an TOUCH command is sent for every slot

        Non-existant keys are ignored.
        Returns the number of keys that were unlinked.
        """
        return self._split_command_across_slots("UNLINK", *keys)


class ClusterManagementCommands(ManagementCommands):
    """
    A class for Redis Cluster management commands

    The class inherits from Redis's core ManagementCommands class and do the
    required adjustments to work with cluster mode
    """

    def slaveof(self, *args, **kwargs):
        raise RedisClusterException("SLAVEOF is not supported in cluster mode")

    def replicaof(self, *args, **kwargs):
        raise RedisClusterException("REPLICAOF is not supported in cluster" " mode")

    def swapdb(self, *args, **kwargs):
        raise RedisClusterException("SWAPDB is not supported in cluster" " mode")


class ClusterDataAccessCommands(DataAccessCommands):
    """
    A class for Redis Cluster Data Access Commands

    The class inherits from Redis's core DataAccessCommand class and do the
    required adjustments to work with cluster mode
    """

    def stralgo(
        self,
        algo,
        value1,
        value2,
        specific_argument="strings",
        len=False,
        idx=False,
        minmatchlen=None,
        withmatchlen=False,
        **kwargs,
    ):
        target_nodes = kwargs.pop("target_nodes", None)
        if specific_argument == "strings" and target_nodes is None:
            target_nodes = "default-node"
        kwargs.update({"target_nodes": target_nodes})
        return super().stralgo(
            algo,
            value1,
            value2,
            specific_argument,
            len,
            idx,
            minmatchlen,
            withmatchlen,
            **kwargs,
        )


class RedisClusterCommands(
    ClusterMultiKeyCommands,
    ClusterManagementCommands,
    ACLCommands,
    PubSubCommands,
    ClusterDataAccessCommands,
    ScriptCommands,
    FunctionCommands,
    RedisModuleCommands,
):
    """
    A class for all Redis Cluster commands

    For key-based commands, the target node(s) will be internally determined
    by the keys' hash slot.
    Non-key-based commands can be executed with the 'target_nodes' argument to
    target specific nodes. By default, if target_nodes is not specified, the
    command will be executed on the default cluster node.


    :param :target_nodes: type can be one of the followings:
        - nodes flag: ALL_NODES, PRIMARIES, REPLICAS, RANDOM
        - 'ClusterNode'
        - 'list(ClusterNodes)'
        - 'dict(any:clusterNodes)'

    for example:
        r.cluster_info(target_nodes=RedisCluster.ALL_NODES)
    """

    def cluster_addslots(self, target_node, *slots):
        """
        Assign new hash slots to receiving node. Sends to specified node.

        :target_node: 'ClusterNode'
            The node to execute the command on
        """
        return self.execute_command(
            "CLUSTER ADDSLOTS", *slots, target_nodes=target_node
        )

    def cluster_countkeysinslot(self, slot_id):
        """
        Return the number of local keys in the specified hash slot
        Send to node based on specified slot_id
        """
        return self.execute_command("CLUSTER COUNTKEYSINSLOT", slot_id)

    def cluster_count_failure_report(self, node_id):
        """
        Return the number of failure reports active for a given node
        Sends to a random node
        """
        return self.execute_command("CLUSTER COUNT-FAILURE-REPORTS", node_id)

    def cluster_delslots(self, *slots):
        """
        Set hash slots as unbound in the cluster.
        It determines by it self what node the slot is in and sends it there

        Returns a list of the results for each processed slot.
        """
        return [self.execute_command("CLUSTER DELSLOTS", slot) for slot in slots]

    def cluster_failover(self, target_node, option=None):
        """
        Forces a slave to perform a manual failover of its master
        Sends to specified node

        :target_node: 'ClusterNode'
            The node to execute the command on
        """
        if option:
            if option.upper() not in ["FORCE", "TAKEOVER"]:
                raise RedisError(
                    f"Invalid option for CLUSTER FAILOVER command: {option}"
                )
            else:
                return self.execute_command(
                    "CLUSTER FAILOVER", option, target_nodes=target_node
                )
        else:
            return self.execute_command("CLUSTER FAILOVER", target_nodes=target_node)

    def cluster_info(self, target_nodes=None):
        """
        Provides info about Redis Cluster node state.
        The command will be sent to a random node in the cluster if no target
        node is specified.
        """
        return self.execute_command("CLUSTER INFO", target_nodes=target_nodes)

    def cluster_keyslot(self, key):
        """
        Returns the hash slot of the specified key
        Sends to random node in the cluster
        """
        return self.execute_command("CLUSTER KEYSLOT", key)

    def cluster_meet(self, host, port, target_nodes=None):
        """
        Force a node cluster to handshake with another node.
        Sends to specified node.
        """
        return self.execute_command(
            "CLUSTER MEET", host, port, target_nodes=target_nodes
        )

    def cluster_nodes(self):
        """
        Force a node cluster to handshake with another node

        Sends to random node in the cluster
        """
        return self.execute_command("CLUSTER NODES")

    def cluster_replicate(self, target_nodes, node_id):
        """
        Reconfigure a node as a slave of the specified master node
        """
        return self.execute_command(
            "CLUSTER REPLICATE", node_id, target_nodes=target_nodes
        )

    def cluster_reset(self, soft=True, target_nodes=None):
        """
        Reset a Redis Cluster node

        If 'soft' is True then it will send 'SOFT' argument
        If 'soft' is False then it will send 'HARD' argument
        """
        return self.execute_command(
            "CLUSTER RESET", b"SOFT" if soft else b"HARD", target_nodes=target_nodes
        )

    def cluster_save_config(self, target_nodes=None):
        """
        Forces the node to save cluster state on disk
        """
        return self.execute_command("CLUSTER SAVECONFIG", target_nodes=target_nodes)

    def cluster_get_keys_in_slot(self, slot, num_keys):
        """
        Returns the number of keys in the specified cluster slot
        """
        return self.execute_command("CLUSTER GETKEYSINSLOT", slot, num_keys)

    def cluster_set_config_epoch(self, epoch, target_nodes=None):
        """
        Set the configuration epoch in a new node
        """
        return self.execute_command(
            "CLUSTER SET-CONFIG-EPOCH", epoch, target_nodes=target_nodes
        )

    def cluster_setslot(self, target_node, node_id, slot_id, state):
        """
        Bind an hash slot to a specific node

        :target_node: 'ClusterNode'
            The node to execute the command on
        """
        if state.upper() in ("IMPORTING", "NODE", "MIGRATING"):
            return self.execute_command(
                "CLUSTER SETSLOT", slot_id, state, node_id, target_nodes=target_node
            )
        elif state.upper() == "STABLE":
            raise RedisError('For "stable" state please use ' "cluster_setslot_stable")
        else:
            raise RedisError(f"Invalid slot state: {state}")

    def cluster_setslot_stable(self, slot_id):
        """
        Clears migrating / importing state from the slot.
        It determines by it self what node the slot is in and sends it there.
        """
        return self.execute_command("CLUSTER SETSLOT", slot_id, "STABLE")

    def cluster_replicas(self, node_id, target_nodes=None):
        """
        Provides a list of replica nodes replicating from the specified primary
        target node.
        """
        return self.execute_command(
            "CLUSTER REPLICAS", node_id, target_nodes=target_nodes
        )

    def cluster_slots(self, target_nodes=None):
        """
        Get array of Cluster slot to node mappings
        """
        return self.execute_command("CLUSTER SLOTS", target_nodes=target_nodes)

    def readonly(self, target_nodes=None):
        """
        Enables read queries.
        The command will be sent to the default cluster node if target_nodes is
        not specified.
        """
        if target_nodes == "replicas" or target_nodes == "all":
            # read_from_replicas will only be enabled if the READONLY command
            # is sent to all replicas
            self.read_from_replicas = True
        return self.execute_command("READONLY", target_nodes=target_nodes)

    def readwrite(self, target_nodes=None):
        """
        Disables read queries.
        The command will be sent to the default cluster node if target_nodes is
        not specified.
        """
        # Reset read from replicas flag
        self.read_from_replicas = False
        return self.execute_command("READWRITE", target_nodes=target_nodes)
