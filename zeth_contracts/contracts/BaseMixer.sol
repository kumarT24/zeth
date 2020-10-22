// Copyright (c) 2015-2020 Clearmatics Technologies Ltd
//
// SPDX-License-Identifier: LGPL-3.0+

pragma solidity ^0.5.0;
pragma experimental ABIEncoderV2;

import "./MerkleTreeMiMC7.sol";

// Declare the ERC20 interface in order to handle ERC20 tokens transfers to and
// from the Mixer. Note that we only declare the functions we are interested in,
// namely, transferFrom() (used to do a Deposit), and transfer() (used to do a
// withdrawal)
contract ERC20 {
    function transferFrom(address from, address to, uint256 value) public;
    function transfer(address to, uint256 value) public;
}

// ERC223 token compatible contract
contract ERC223ReceivingContract {
    // See:
    //   https://github.com/Dexaran/ERC223-token-standard/blob/Recommended/Receiver_Interface.sol
    struct Token {
        address sender;
        uint256 value;
        bytes data;
        bytes4 sig;
    }

    function tokenFallback(address from, uint256 value, bytes memory data)
        public pure {
        Token memory tkn;
        tkn.sender = from;
        tkn.value = value;
        tkn.data = data;

         // See:
         //   https://solidity.readthedocs.io/en/v0.5.5/types.html#conversions-between-elementary-types
        uint32 u =
            uint32(bytes4(data[0])) +
            uint32(bytes4(data[1]) >> 8) +
            uint32(bytes4(data[2]) >> 16) +
            uint32(bytes4(data[3]) >> 24);
        tkn.sig = bytes4(u);
    }
}

// BaseMixer implements the functions shared across all Mixers (regardless which
// zkSNARK is used)
contract BaseMixer is MerkleTreeMiMC7, ERC223ReceivingContract {

    // The roots of the different updated trees
    mapping(bytes32 => bool) roots;

    // The public list of nullifiers (prevents double spend)
    mapping(bytes32 => bool) nullifiers;

    // JoinSplit description, gives the number of inputs (nullifiers) and
    // outputs (commitments/ciphertexts) to receive and process.
    //
    // IMPORTANT NOTE: We need to employ the same JS configuration than the one
    // used in the cpp prover. Here we use 2 inputs and 2 outputs (it is a 2-2
    // JS).
    uint256 constant jsIn = 2; // Number of nullifiers
    uint256 constant jsOut = 2; // Number of commitments/ciphertexts

    // Size of the public values in bits
    uint256 constant public_value_bits = 64;

    // Public values mask
    uint256 constant public_value_mask = (1 << public_value_bits) - 1;

    // Total number of bits for public values. Digest residual bits appear
    // after these.
    uint256 constant total_public_value_bits = 2 * public_value_bits;

    uint256 constant digest_length = 256;

    // Number of hash digests in the primary inputs:
    //   1 (the root)
    //   2 * jsIn (nullifier and message auth tag per JS input)
    //   jsOut (commitment per JS output)
    uint256 constant num_hash_digests = 1 + 2 * jsIn;

    // All code assumes that public values and residual bits can be encoded in
    // a single field element.
    uint256 constant num_field_residual = 1;

    // The number of public inputs are:
    // - 1 (the root)
    // - jsIn (the nullifiers)
    // - jsOut (the commitments)
    // - 1 (hsig)
    // - JsIn (the message auth. tags)
    // - num_field_residual (the residual bits not fitting in a single field
    //   element and the in and out public values)
    uint256 constant num_inputs = 1 + jsOut + num_hash_digests + num_field_residual;

    // Contract variable that indicates the address of the token contract
    // If token = address(0) then the mixer works with ether
    address public token;

    // The unit used for public values (ether in and out), in Wei. Must match
    // the python wrappers. Use Szabos (10^12 Wei).
    uint64 constant public_unit_value_wei = 1 szabo;

    // solium complains if the parameters here are indented.
    event LogMix(
    bytes32 root,
    bytes32[jsIn] nullifiers,
    bytes32[jsOut] commitments,
    bytes[jsOut] ciphertexts);

    // Debug only
    event LogDebug(string message);

    // Constructor
    constructor(uint256 depth, address token_address)
        MerkleTreeMiMC7(depth) public
    {
        bytes32 initialRoot = nodes[0];
        roots[initialRoot] = true;

        token = token_address;
    }

    // Function allowing external users of the contract to retrieve some of the
    // constants used in the mixer (since the solidity interfaces do not export
    // this information as-of the current version). The intention is that
    // external users and contraacts can query this function and ensure that
    // they are compatible with the mixer configurations.
    //
    // Returns the number of input notes, the number of output notes and the
    // total number of
    function get_constants()
        external pure
        returns (uint256 js_in_out, uint256 js_out_out, uint256 num_inputs_out)
    {
        js_in_out = jsIn;
        js_out_out = jsOut;
        num_inputs_out = num_inputs;
    }

    // ====================================================================== //
    // Reminder: Remember that the primary inputs are ordered as follows:
    //
    //   [Root, CommitmentS, NullifierS, h_sig, h_iS, Residual Field Element(S)]
    //
    // ie, below is the index mapping of the primary input elements on the
    // protoboard:
    //
    //   <Merkle Root>               0
    //   <Commitment[0]>             1
    //   ...
    //   <Commitment[jsOut - 1]>     jsOut
    //   <Nullifier[0]>              jsOut + 1
    //   ...
    //   <Nullifier[jsIn]>           jsOut + jsIn
    //   <h_sig>                     jsOut + jsIn + 1
    //   <Message Auth Tag[0]>       jsOut + jsIn + 2
    //   ...
    //   <Message Auth Tag[jsIn]>    jsOut + 2*jsIn + 1
    //   <Residual Field Elements>   jsOut + 2*jsIn + 2
    //
    // The Residual field elements are structured as follows:
    //
    //   255                                         128         64           0
    //   |<empty>|<h_sig>|<nullifiers>|<msg_auth_tags>|<v_pub_in>)|<v_pub_out>|
    //
    // where each entry entry after public output and input holds the
    // (curve-specific) number residual bits for the corresponding 256 bit
    // value.

    // Utility function to extract a full uint256 from a field element and the
    // n-th set of residual bits from `residual`. This function is
    // curve-dependent.
    function extract_bytes32(
        uint256 field_element, uint256 residual, uint256 residual_bits_set_idx)
        internal pure
        returns(bytes32);

    // This function is used to extract the public values (vpub_in, vpub_out)
    // from the residual field element(S)
    function assemble_public_values(uint256 residual_bits)
        public pure
        returns (uint256 vpub_in, uint256 vpub_out)
    {
        // vpub_out and vpub_in occupy the first and second public_value_bits
        vpub_out = (residual_bits & public_value_mask) * public_unit_value_wei;
        vpub_in = ((residual_bits >> public_value_bits) & public_value_mask)
        * public_unit_value_wei;
    }

    // This function is used to reassemble hsig given the the primary_inputs To
    // do so, we extract the remaining bits of hsig from the residual field
    // element(S) and combine them with the hsig field element
    function assemble_hsig(uint256[num_inputs] memory primary_inputs) public pure
    returns(bytes32 hsig)
    {
        // The h_sig residual bits are after the jsIn authentication tags and
        // jsIn nullifier bits.
        return extract_bytes32(
            primary_inputs[1 + jsIn + jsOut],
            primary_inputs[1 + jsOut + num_hash_digests],
            2 * jsIn);
    }

    // This function is used to reassemble the nullifiers given the nullifier
    // index [0, jsIn[ and the primary_inputs To do so, we extract the
    // remaining bits of the nullifier from the residual field element(S) and
    // combine them with the nullifier field element
    function assemble_nullifier(
        uint256 index, uint256[num_inputs] memory primary_inputs) public pure
    returns(bytes32 nf)
    {
        // We first check that the nullifier we want to retrieve exists
        require(index < jsIn, "nullifier index overflow");

        // Nullifier residual bits follow the jsIn message authentication tags.
        return extract_bytes32(
            primary_inputs[1 + jsOut + index],
            primary_inputs[1 + jsOut + num_hash_digests],
            jsIn + index);
    }

    // This function processes the primary inputs to append and check the root
    // and nullifiers in the primary inputs (instance) and modifies the state
    // of the mixer contract accordingly. (ie: Appends the commitments to the
    // tree, appends the nullifiers to the list and so on).
    function check_mkroot_nullifiers_hsig_append_nullifiers_state(
        uint256[4] memory vk,
        uint256[num_inputs] memory primary_inputs,
        bytes32[jsIn] memory nfs)
        internal {
        // 1. We re-assemble the full root digest and check it is in the tree
        require(
            roots[bytes32(primary_inputs[0])],
            "Invalid root: This root doesn't exist"
        );

        // 2. We re-assemble the nullifiers (JSInputs) and check they were not
        // already seen.
        for (uint256 i = 0; i < jsIn; i++) {
            bytes32 nullifier = assemble_nullifier(i, primary_inputs);
            require(
                !nullifiers[nullifier],
                "Invalid nullifier: This nullifier has already been used"
            );
            nullifiers[nullifier] = true;

            nfs[i] = nullifier;
        }

        // 3. We re-compute h_sig, re-assemble the expected h_sig and check
        // they are equal (i.e. that h_sig re-assembled was correctly generated
        // from vk).
        bytes32 expected_hsig = sha256(abi.encodePacked(nfs, vk));
        bytes32 hsig = assemble_hsig(primary_inputs);
        require(
            expected_hsig == hsig,
            "Invalid hsig: This hsig does not correspond to the hash of vk and the nfs"
        );
    }

    function assemble_commitments_and_append_to_state(
        uint256[num_inputs] memory primary_inputs,
        bytes32[jsOut] memory comms)
        internal {
        // We re-assemble the commitments (JSOutputs)
        for (uint256 i = 0; i < jsOut; i++) {
            bytes32 current_commitment = bytes32(primary_inputs[1 + i]);
            comms[i] = current_commitment;
            insert(current_commitment);
        }
    }

    function process_public_values(uint256[num_inputs] memory primary_inputs)
        internal {
        // We get vpub_in and vpub_out in wei
        (uint256 vpub_in, uint256 vpub_out) =
            assemble_public_values(primary_inputs[1 + jsOut + num_hash_digests]);

        // If the vpub_in is > 0, we need to make sure the right amount is paid
        if (vpub_in > 0) {
            if (token != address(0)) {
                ERC20 erc20Token = ERC20(token);
                erc20Token.transferFrom(msg.sender, address(this), vpub_in);
            } else {
                require(
                    msg.value == vpub_in,
                    "Wrong msg.value: Value paid is not correct"
                );
            }
        } else {
            // If vpub_in = 0, return incoming Ether to the caller
            if (msg.value > 0) {
                (bool success, ) = msg.sender.call.value(msg.value)("");
                require(success, "vpub_in return transfer failed");
            }
        }

        // If value_pub_out > 0 then we do a withdraw.  We retrieve the
        // msg.sender and send him the appropriate value IF proof is valid
        if (vpub_out > 0) {
            if (token != address(0)) {
                ERC20 erc20Token = ERC20(token);
                erc20Token.transfer(msg.sender, vpub_out);
            } else {
                (bool success, ) = msg.sender.call.value(vpub_out)("");
                require(success, "vpub_out transfer failed");
            }
        }
    }

    function add_merkle_root(bytes32 root) internal {
        roots[root] = true;
    }
}
