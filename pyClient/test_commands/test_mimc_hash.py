#!/usr/bin/env python3

from zeth.mimc import MiMC7, keccak_256
from zeth.constants import MIMC_MT_SEED
import zeth.contracts as contracts


def test_mimc_round() -> None:
    m = MiMC7("Clearmatics")
    x = 340282366920938463463374607431768211456
    k = 28948022309329048855892746252171976963317496166410141009864396001978282409983  # noqa
    c = 14220067918847996031108144435763672811050758065945364308986253046354060608451  # noqa
    assert m.mimc_round(x, k, c) == \
        7970444205539657036866618419973693567765196138501849736587140180515018751924  # noqa
    print("Test Round passed")


def test_keccak256() -> None:
    assert keccak_256("Clearmatics") == \
        14220067918847996031108144435763672811050758065945364308986253046354060608451  # noqa
    print("Test keccak256 passed")


def test_mimc_encrypt() -> None:
    # Generating test vector for MiMC encrypt
    m = MiMC7()
    ct = m.mimc_encrypt(
      3703141493535563179657531719960160174296085208671919316200479060314459804651,  # noqa
      15683951496311901749339509118960676303290224812129752890706581988986633412003)  # noqa
    print("Ciphertext:")
    print(ct)

    # Generating test vector for MiMC Hash
    m = MiMC7()
    digest = m.mimc_mp(
        3703141493535563179657531719960160174296085208671919316200479060314459804651,  # noqa
        15683951496311901749339509118960676303290224812129752890706581988986633412003)  # noqa
    print("Hash result:")
    print(digest)

    # Generating test vectors for testing the MiMC Merkle Tree contract
    print("Test vector for testMimCHash")

    res = m.mimc_mp(0, 0)
    print("Level 2")
    print(res)

    res = m.mimc_mp(res, res)
    print("Level 1")
    print(res)

    res = m.mimc_mp(res, res)
    print("Root")
    print(res)


def test_mimc() -> None:
    """
    Benchmarking Gas cost for different MiMC exponents
    """

    # compile MiMC, MerkleTree contracts
    mimc_interface, tree_interface = contracts.compile_util_contracts()

    # deploy MimC contract
    mimc_instance, mimc_address = contracts.deploy_mimc_contract(mimc_interface)

    # deploy MerkleTreeMiMCHash contract
    tree_instance = \
        contracts.deploy_tree_contract(tree_interface, 3, mimc_address)

    # Test vector generated by using pyClient/zeth/mimc.py
    x = 3703141493535563179657531719960160174296085208671919316200479060314459804651  # noqa

    y = 15683951496311901749339509118960676303290224812129752890706581988986633412003  # noqa

    out = 16797922449555994684063104214233396200599693715764605878168345782964540311877  # noqa

    # Test vectors generated by using pyClient/zeth/mimc.py
    root = 2441541000495724811029127871318798691502895708150678885895101469991191938081  # noqa
    level_1 = 18994625108571498039763404178311223352138299208457461470344693108622989074396  # noqa
    level_2 = 14099405974798296289089207144580827488086367232485193855461335777570080506647  # noqa

    # MiMC contract unit test
    digest = contracts.mimc_hash(
        mimc_instance,
        x.to_bytes(32, byteorder="big"),
        y.to_bytes(32, byteorder="big"),
        MIMC_MT_SEED.encode())

    assert int.from_bytes(digest, byteorder="big") == out, \
        "Hash is NOT correct"

    # MerkleTreeMiMCHash of depth 3 unit test

    # Recover root and merkle tree from the contract
    tree = contracts.get_merkle_tree(tree_instance)
    recovered_root = contracts.get_merkle_root(tree_instance)

    # Leaves
    for i in range(7, 15):
        assert int.from_bytes(tree[i], byteorder="big") == 0, \
            "MerkleTree Error Leaves"

    # Level 2
    for i in range(3, 7):
        assert int.from_bytes(tree[i], byteorder="big") == level_2, \
            "MerkleTree Error Level 2"

    # Level 1
    for i in range(1, 3):
        assert int.from_bytes(tree[i], byteorder="big") == level_1, \
            "MerkleTree Error Level 1"

    # Root
    assert int.from_bytes(tree[0], byteorder="big") == root, \
        "MerkleTree Error Root"

    # Recovered root
    assert int.from_bytes(recovered_root, byteorder="big") == root, \
        "MerkleTree Error Computed Root"


if __name__ == "__main__":
    test_mimc_round()
    test_keccak256()
    test_mimc_encrypt()
    test_mimc()
    print("All test passed")
