from __future__ import annotations
import zeth.constants as constants
import zeth.errors as errors
from zeth.utils import get_trusted_setup_dir, hex_extend_32bytes, \
    hex_digest_to_binary_string, string_list_flatten, encode_single, \
    encode_abi, encrypt, decrypt, get_public_key_from_bytes
from zeth.prover_client import ProverClient
from api.util_pb2 import ZethNote, JoinsplitInput, HexPointBaseGroup1Affine, \
    HexPointBaseGroup2Affine
from nacl.public import PrivateKey, PublicKey  # type: ignore
import nacl.utils  # type: ignore
import api.prover_pb2 as prover_pb2

import os
import json
import sys
from Crypto import Random
from hashlib import blake2s, sha256
from py_ecc import bn128 as ec
from typing import Tuple, Dict, List, Iterable, Union, Any


FQ = ec.FQ
G1 = Tuple[ec.FQ, ec.FQ]


class ApkAskPair:
    def __init__(self, a_sk: str, a_pk: str):
        self.a_pk = a_pk
        self.a_sk = a_sk


# Joinsplit Secret Key
JoinsplitSecretKey = Tuple[FQ, FQ]


# Joinsplit Public Key
JoinsplitPublicKey = Tuple[G1, G1]


class JoinsplitKeypair:
    """
    A Joinsplit secret and public keypair.
    """
    def __init__(self, x: FQ, y: FQ, x_g1: G1, y_g1: G1):
        self.vk = (x_g1, y_g1)
        self.sk = (x, y)


# Dictionary representing a VerificationKey from any supported snark
GenericVerificationKey = Dict[str, Any]


# Dictionary representing a Proof from any supported snark
GenericProof = Dict[str, Any]


def create_zeth_notes(
        phi: str,
        hsig: str,
        recipient_apk0: str,
        value0: str,
        recipient_apk1: str,
        value1: str) -> Tuple[ZethNote, ZethNote]:
    """
    Create two ordered ZethNotes. This function is used to generate new output
    notes.
    """
    rho0 = compute_rho_i(phi, hsig, 0)
    trap_r0 = trap_r_randomness()
    note0 = ZethNote(
        apk=recipient_apk0,
        value=value0,
        rho=rho0,
        trap_r=trap_r0
    )

    rho1 = compute_rho_i(phi, hsig, 1)
    trap_r1 = trap_r_randomness()
    note1 = ZethNote(
        apk=recipient_apk1,
        value=value1,
        rho=rho1,
        trap_r=trap_r1
    )

    return note0, note1


def parse_zeth_note(zeth_note_grpc_obj: ZethNote) -> Dict[str, str]:
    note_json = {
        "a_pk": zeth_note_grpc_obj.apk,
        "value": zeth_note_grpc_obj.value,
        "rho": zeth_note_grpc_obj.rho,
        "trap_r": zeth_note_grpc_obj.trap_r,
    }
    return note_json


def zeth_note_obj_from_parsed(parsed_zeth_note: Dict[str, str]) -> ZethNote:
    note = ZethNote(
        apk=parsed_zeth_note["a_pk"],
        value=parsed_zeth_note["value"],
        rho=parsed_zeth_note["rho"],
        trap_r=parsed_zeth_note["trap_r"]
    )
    return note


def compute_commitment(zeth_note_grpc_obj: ZethNote) -> str:
    """
    Used by the recipient of a payment to recompute the commitment and check
    the membership in the tree to confirm the validity of a payment
    """
    # inner_k = blake2s(a_pk || rho)
    inner_k = blake2s(
        encode_abi(
            ['bytes32', 'bytes32'],
            [bytes.fromhex(zeth_note_grpc_obj.apk),
             bytes.fromhex(zeth_note_grpc_obj.rho)])
    ).hexdigest()

    # outer_k = blake2s(r || [inner_k]_128)
    first_128bits_inner_comm = inner_k[0:128]
    outer_k = blake2s(
        encode_abi(
            ['bytes', 'bytes'],
            [bytes.fromhex(zeth_note_grpc_obj.trap_r),
             bytes.fromhex(first_128bits_inner_comm)])).hexdigest()

    # cm = blake2s(outer_k || 0^192 || value_v)
    front_pad = "000000000000000000000000000000000000000000000000"
    cm = blake2s(
        encode_abi(
            ["bytes32", "bytes32"],
            [bytes.fromhex(outer_k),
             bytes.fromhex(front_pad + zeth_note_grpc_obj.value)])
    ).hexdigest()
    return cm


def compute_nullifier(zeth_note: ZethNote, spending_authority_ask: str) -> str:
    """
    Returns nf = blake2s(1110 || [a_sk]_252 || rho)
    """
    binary_ask = hex_digest_to_binary_string(spending_authority_ask)
    first_252bits_ask = binary_ask[:252]
    left_leg_bin = "1110" + first_252bits_ask
    left_leg_hex = "{0:0>4X}".format(int(left_leg_bin, 2))
    nullifier = blake2s(
        encode_abi(
            ["bytes32", "bytes32"],
            [bytes.fromhex(left_leg_hex), bytes.fromhex(zeth_note.rho)])
    ).hexdigest()
    return nullifier


def compute_rho_i(phi: str, hsig: str, i: int) -> str:
    """
    Returns rho_i = blake2s(0 || i || 10 || [phi]_252 || hsig)
    See: Zcash protocol spec p. 57, Section 5.4.2 Pseudo Random Functions
    """

    # [SANITY CHECK] make sure i is in the interval [0, 1]
    # Since we only allow for 2 input notes in the joinsplit
    assert i < constants.JS_INPUTS

    # Append PRF^{rho} tag to a_sk
    binary_phi = hex_digest_to_binary_string(phi)
    first_252bits_phi = binary_phi[:252]
    left_leg_bin = "0" + str(i) + "10" + first_252bits_phi
    left_leg_hex = "{0:0>4X}".format(int(left_leg_bin, 2))

    rho_i = blake2s(
        encode_abi(
            ["bytes32", "bytes32"],
            [bytes.fromhex(left_leg_hex), bytes.fromhex(hsig)])
    ).hexdigest()
    return rho_i


def derive_apk(ask: str) -> str:
    """
    Returns a_pk = blake2s(1100 || [a_sk]_252 || 0^256)
    """
    binary_ask = hex_digest_to_binary_string(ask)
    first_252bits_ask = binary_ask[:252]
    left_leg_bin = "1100" + first_252bits_ask
    left_leg_hex = "{0:0>4X}".format(int(left_leg_bin, 2))
    zeroes = "0000000000000000000000000000000000000000000000000000000000000000"
    apk = blake2s(
        encode_abi(
            ["bytes32", "bytes32"],
            [bytes.fromhex(left_leg_hex), bytes.fromhex(zeroes)])
    ).hexdigest()
    return apk


def gen_apk_ask_keypair() -> ApkAskPair:
    a_sk = bytes(Random.get_random_bytes(32)).hex()
    a_pk = derive_apk(a_sk)
    keypair = ApkAskPair(a_sk, a_pk)
    return keypair


def create_joinsplit_input(
        merkle_path: List[str],
        address: int,
        note: ZethNote,
        ask: str,
        nullifier: str) -> JoinsplitInput:
    return JoinsplitInput(
        merkle_path=merkle_path,
        address=address,
        note=note,
        spending_ask=ask,
        nullifier=nullifier
    )


def gen_one_time_schnorr_vk_sk_pair() -> JoinsplitKeypair:
    x = FQ(
        int(bytes(Random.get_random_bytes(32)).hex(), 16) % constants.ZETH_PRIME)
    X = ec.multiply(ec.G1, x.n)
    y = FQ(
        int(bytes(Random.get_random_bytes(32)).hex(), 16) % constants.ZETH_PRIME)
    Y = ec.multiply(ec.G1, y.n)
    return JoinsplitKeypair(x, y, X, Y)


def encode_pub_input_to_hash(message_list: List[Union[str, List[str]]]) -> bytes:
    """
    Encode the primary inputs as defined in ZCash chapter 4.15.1 into a byte
    array (https://github.com/zcash/zips/blob/master/protocol/protocol.pdf) The
    root, nullifierS, commitmentS, h_sig and h_iS are encoded over two field
    elements The public values are encoded over one field element
    """
    input_sha = bytearray()

    # Flatten the input list
    messages = string_list_flatten(message_list)

    # Encode the given Merkle Tree root
    root = hex_extend_32bytes(messages[0][2:])
    root_encoded = encode_single("bytes32", bytes.fromhex(root))
    input_sha += root_encoded

    # Encode the given input nullifiers
    for i in range(1, 1 + 2*(constants.JS_INPUTS), 2):
        nf = field_elements_to_hex(messages[i], messages[i+1])
        nf_encoded = encode_single("bytes32", bytes.fromhex(nf))
        input_sha += nf_encoded

    # Encode the given output commitments
    for i in range(
            1 + 2*(constants.JS_INPUTS),
            1 + 2*(constants.JS_INPUTS + constants.JS_OUTPUTS), 2):
        cm = field_elements_to_hex(messages[i], messages[i+1])
        cm_encoded = encode_single("bytes32", bytes.fromhex(cm))
        input_sha += cm_encoded

    # Encode the public value in
    v_in = messages[1 + 2*(constants.JS_INPUTS + constants.JS_OUTPUTS)][2:]
    v_in = hex_extend_32bytes(v_in)
    vin_encoded = encode_single("bytes32", bytes.fromhex(v_in))
    input_sha += vin_encoded

    # Encode the public value out
    v_out = messages[1 + 2*(constants.JS_INPUTS + constants.JS_OUTPUTS) + 1][2:]
    v_out = hex_extend_32bytes(v_out)
    vout_encoded = encode_single("bytes32", bytes.fromhex(v_out))
    input_sha += vout_encoded

    # Encode the h_sig
    hsig = field_elements_to_hex(
        messages[1 + 2*(constants.JS_INPUTS + constants.JS_OUTPUTS) + 1 + 1],
        messages[1 + 2*(constants.JS_INPUTS + constants.JS_OUTPUTS) + 1 + 1 + 1])
    hsig_encoded = encode_single("bytes32", bytes.fromhex(hsig))
    input_sha += hsig_encoded

    # Encode the h_iS
    for i in range(
        1 + 2*(constants.JS_INPUTS + constants.JS_OUTPUTS + 1 + 1),
        1 + 2*(constants.JS_INPUTS + constants.JS_OUTPUTS + 1 + 1 +
               constants.JS_INPUTS),
        2
    ):
        h_i = field_elements_to_hex(messages[i], messages[i+1])
        h_i_encoded = encode_single("bytes32", bytes.fromhex(h_i))
        input_sha += h_i_encoded

    return input_sha


def field_elements_to_hex(longfield: str, shortfield: str) -> str:
    """
    Encode a 256 bit array written over two field elements into a single 32
    byte long hex

    if A= x0 ... x255 and B = y0 ... y7, returns R = hex(x255 ... x3 || y7 y6 y5)
    """
    # Convert longfield into a 253 bit long array
    long_bit = "{0:b}".format(int(longfield, 16))
    if len(long_bit) > 253:
        long_bit = long_bit[:253]
    long_bit = "0"*(253-len(long_bit)) + long_bit

    # Convert shortfield into a 3 bit long array
    short_bit = "{0:b}".format(int(shortfield, 16))
    if len(short_bit) < 3:
        short_bit = "0"*(3-len(short_bit)) + short_bit

    # Reverse the bit arrays
    reversed_long = long_bit[::-1]
    reversed_short = short_bit[::-1]

    # Fill the result 256 bit long array
    res = reversed_long[:253]
    res += reversed_short[:3]
    res = hex_extend_32bytes("{0:0>4X}".format(int(res, 2)))

    return res


def sign(
        keypair: JoinsplitKeypair,
        hash_ciphers: str,
        hash_proof: str,
        hash_inputs: str) -> int:
    """
    Generate a Schnorr one-time signature of the ciphertexts, proofs and
    primary inputs We chose to sign the hash of the proof for modularity (to
    use the same code regardless of whether GROTH16 or PGHR13 proof system is
    chosen), and sign the hash of the ciphers and inputs for consistency.
    """
    # Parse the signature key pair
    vk = keypair.vk
    sk = keypair.sk

    # Format part of the public key as an hex
    y0_hex = hex_extend_32bytes("{0:0>4X}".format(int(vk[1][0])))
    y1_hex = hex_extend_32bytes("{0:0>4X}".format(int(vk[1][1])))

    # Encode and hash the verifying key and input hashes
    data_to_sign = encode_abi(
        ["bytes32", "bytes32", "bytes32", "bytes32", "bytes32"],
        [
            bytes.fromhex(y0_hex),
            bytes.fromhex(y1_hex),
            bytes.fromhex(hash_ciphers),
            bytes.fromhex(hash_proof),
            bytes.fromhex(hash_inputs)
        ]
    )
    data_hex = sha256(data_to_sign).hexdigest()

    # Convert the hex digest into a field element
    h = int(data_hex, 16) % constants.ZETH_PRIME

    # Compute the signature sigma
    sigma = sk[1].n + h * sk[0].n % constants.ZETH_PRIME

    return sigma


def parse_verification_key_pghr13(
        vk_obj: prover_pb2.VerificationKey) -> GenericVerificationKey:
    vk = vk_obj.pghr13_verification_key
    return {
        "a": _parse_hex_point_base_group2_affine(vk.a),
        "b": _parse_hex_point_base_group1_affine(vk.b),
        "c": _parse_hex_point_base_group2_affine(vk.c),
        "g": _parse_hex_point_base_group2_affine(vk.gamma),
        "gb1": _parse_hex_point_base_group1_affine(vk.gamma_beta_g1),
        "gb2": _parse_hex_point_base_group2_affine(vk.gamma_beta_g2),
        "z": _parse_hex_point_base_group2_affine(vk.z),
        "IC": json.loads(vk.ic),
    }


def parse_verification_key_groth16(
        vk_obj: prover_pb2.VerificationKey) -> GenericVerificationKey:
    vk = vk_obj.groth16_verification_key
    return {
        "alpha_g1": _parse_hex_point_base_group1_affine(vk.alpha_g1),
        "beta_g2": _parse_hex_point_base_group2_affine(vk.beta_g2),
        "delta_g2": _parse_hex_point_base_group2_affine(vk.delta_g2),
        "abc_g1": json.loads(vk.abc_g1),
    }


def parse_verification_key(
        vk_obj: prover_pb2.VerificationKey,
        zksnark: str) -> GenericVerificationKey:
    if zksnark == constants.PGHR13_ZKSNARK:
        return parse_verification_key_pghr13(vk_obj)
    if zksnark == constants.GROTH16_ZKSNARK:
        return parse_verification_key_groth16(vk_obj)
    return sys.exit(errors.SNARK_NOT_SUPPORTED)


def write_verification_key(
        vk_obj: prover_pb2.VerificationKey,
        zksnark: str) -> None:
    """
    Writes the verification key (object) in a json file
    """
    vk_json = parse_verification_key(vk_obj, zksnark)
    setup_dir = get_trusted_setup_dir()
    filename = os.path.join(setup_dir, "vk.json")
    with open(filename, 'w') as outfile:
        json.dump(vk_json, outfile)


def parse_proof_pghr13(proof_obj: prover_pb2.ExtendedProof) -> GenericProof:
    proof = proof_obj.pghr13_extended_proof
    return {
        "a": _parse_hex_point_base_group1_affine(proof.a),
        "a_p": _parse_hex_point_base_group1_affine(proof.a_p),
        "b": _parse_hex_point_base_group2_affine(proof.b),
        "b_p": _parse_hex_point_base_group1_affine(proof.b_p),
        "c": _parse_hex_point_base_group1_affine(proof.c),
        "c_p": _parse_hex_point_base_group1_affine(proof.c_p),
        "h": _parse_hex_point_base_group1_affine(proof.h),
        "k": _parse_hex_point_base_group1_affine(proof.k),
        "inputs": json.loads(proof.inputs),
    }


def parse_proof_groth16(proof_obj: prover_pb2.ExtendedProof) -> GenericProof:
    proof = proof_obj.groth16_extended_proof
    return {
        "a": _parse_hex_point_base_group1_affine(proof.a),
        "b": _parse_hex_point_base_group2_affine(proof.b),
        "c": _parse_hex_point_base_group1_affine(proof.c),
        "inputs": json.loads(proof.inputs),
    }


def parse_proof(
        proof_obj: prover_pb2.ExtendedProof,
        zksnark: str) -> GenericProof:
    if zksnark == constants.PGHR13_ZKSNARK:
        return parse_proof_pghr13(proof_obj)
    if zksnark == constants.GROTH16_ZKSNARK:
        return parse_proof_groth16(proof_obj)
    return sys.exit(errors.SNARK_NOT_SUPPORTED)


def compute_joinsplit2x2_inputs(
        mk_root: str,
        input_note0: ZethNote,
        input_address0: int,
        mk_path0: List[str],
        input_note1: ZethNote,
        input_address1: int,
        mk_path1: List[str],
        sender_ask: str,
        recipient0_apk: str,
        recipient1_apk: str,
        output_note_value0: str,
        output_note_value1: str,
        public_in_value: str,
        public_out_value: str,
        joinsplit_vk: JoinsplitPublicKey) -> prover_pb2.ProofInputs:
    """
    Create a ProofInput object for joinsplit parameters
    """
    input_nullifier0 = compute_nullifier(input_note0, sender_ask)
    input_nullifier1 = compute_nullifier(input_note1, sender_ask)
    js_inputs = [
        create_joinsplit_input(
            mk_path0, input_address0, input_note0, sender_ask, input_nullifier0),
        create_joinsplit_input(
            mk_path1, input_address1, input_note1, sender_ask, input_nullifier1)
    ]

    random_seed = _signature_randomness()
    h_sig = _compute_h_sig(
        random_seed,
        input_nullifier0,
        input_nullifier1,
        joinsplit_vk)
    phi = _transaction_randomness()

    output_note0, output_note1 = create_zeth_notes(
        phi,
        h_sig,
        recipient0_apk,
        output_note_value0,
        recipient1_apk,
        output_note_value1
    )

    js_outputs = [
        output_note0,
        output_note1
    ]

    return prover_pb2.ProofInputs(
        mk_root=mk_root,
        js_inputs=js_inputs,
        js_outputs=js_outputs,
        pub_in_value=public_in_value,
        pub_out_value=public_out_value,
        h_sig=h_sig,
        phi=phi)


def get_proof_joinsplit_2_by_2(
        prover_client: ProverClient,
        mk_root: str,
        input_note0: ZethNote,
        input_address0: int,
        mk_path0: List[str],
        input_note1: ZethNote,
        input_address1: int,
        mk_path1: List[str],
        sender_ask: str,
        recipient0_apk: str,
        recipient1_apk: str,
        output_note_value0: str,
        output_note_value1: str,
        public_in_value: str,
        public_out_value: str,
        zksnark: str
) -> Tuple[ZethNote, ZethNote, Dict[str, Any], JoinsplitKeypair]:
    """
    Query the prover server to generate a proof for the given joinsplit
    parameters.
    """
    joinsplit_keypair = gen_one_time_schnorr_vk_sk_pair()
    proof_input = compute_joinsplit2x2_inputs(
        mk_root,
        input_note0,
        input_address0,
        mk_path0,
        input_note1,
        input_address1,
        mk_path1,
        sender_ask,
        recipient0_apk,
        recipient1_apk,
        output_note_value0,
        output_note_value1,
        public_in_value,
        public_out_value,
        joinsplit_keypair.vk)
    proof_obj = prover_client.get_proof(proof_input)
    proof_json = parse_proof(proof_obj, zksnark)

    # We return the zeth notes to be able to spend them later
    # and the proof used to create them
    return (
        proof_input.js_outputs[0],  # pylint: disable=no-member
        proof_input.js_outputs[1],  # pylint: disable=no-member
        proof_json,
        joinsplit_keypair)


def encrypt_notes(
        notes: List[Tuple[ZethNote, PublicKey]]) -> Tuple[bytes, List[bytes]]:
    """
    Encrypts a set of output notes to be decrypted by the respective receivers.
    Returns the senders (ephemeral) public key (encoded as bytes) and the
    ciphertexts corresponding to each note.
    """
    # generate ephemeral ec25519 key
    eph_sk = PrivateKey.generate()

    def _encrypt_note(out_note: ZethNote, pub_key: PublicKey) -> bytes:
        out_note_str = json.dumps(parse_zeth_note(out_note))
        return encrypt(out_note_str, pub_key, eph_sk)

    pk_sender = eph_sk.public_key.encode(encoder=nacl.encoding.RawEncoder)
    ciphertexts = [_encrypt_note(note, pk) for (note, pk) in notes]
    return (pk_sender, ciphertexts)


def receive_notes(
        ciphertexts: List[bytes],
        pk_sender_enc: bytes,
        sk_receiver: PrivateKey) -> Iterable[ZethNote]:
    """
    Given the receivers secret key, and the event data from a transaction
    (encrypted notes), decrypt any that are intended for the receiver.
    """
    pk_sender: PublicKey = get_public_key_from_bytes(pk_sender_enc)

    for ciphertext in ciphertexts:
        try:
            plaintext = decrypt(ciphertext, pk_sender, sk_receiver)
            yield zeth_note_obj_from_parsed(json.loads(plaintext))
        except Exception as e:
            print(f"receive_notes: error: {e}")
            continue


def _compute_h_sig(
        random_seed: bytes,
        nf0: str,
        nf1: str,
        joinsplit_pub_key: JoinsplitPublicKey) -> str:
    """
    Compute h_sig = blake2s(randomSeed, nf0, nf1, joinSplitPubKey)
    Flatten the verification key
    """
    js_pub_key_hex = [item for sublist in joinsplit_pub_key for item in sublist]

    vk_hex = ""
    for item in js_pub_key_hex:
        # For each element of the list, convert it to an hex and append it
        vk_hex += hex_extend_32bytes("{0:0>4X}".format(int(item)))

    h_sig = blake2s(
        encode_abi(
            ['bytes32', 'bytes32', 'bytes32', 'bytes'],
            [
                random_seed,
                bytes.fromhex(nf0),
                bytes.fromhex(nf1),
                bytes.fromhex(vk_hex)
            ]
        )
    ).hexdigest()

    return h_sig


def trap_r_randomness() -> str:
    """
    Compute randomness "r" as 48 random bytes
    """
    return bytes(Random.get_random_bytes(48)).hex()


def _signature_randomness() -> bytes:
    """
    Compute the signature randomness "randomSeed", used for computing h_sig
    """
    return bytes(Random.get_random_bytes(32))


def _transaction_randomness() -> str:
    """
    Compute the transaction randomness "phi", used for computing the new rhoS
    """
    return bytes(Random.get_random_bytes(32)).hex()


def _parse_hex_point_base_group1_affine(
        point: HexPointBaseGroup1Affine) -> Tuple[str, str]:
    return (point.x_coord, point.y_coord)


def _parse_hex_point_base_group2_affine(
        point: HexPointBaseGroup2Affine
) -> Tuple[Tuple[str, str], Tuple[str, str]]:
    return (
        (point.x_c1_coord, point.x_c0_coord),
        (point.y_c1_coord, point.y_c0_coord))
