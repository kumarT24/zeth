// Copyright (c) 2015-2021 Clearmatics Technologies Ltd
//
// SPDX-License-Identifier: LGPL-3.0+

#include "simple_test.hpp"

#include "core/utils.hpp"
#include "libzeth/serialization/r1cs_serialization.hpp"
#include "libzeth/snarks/groth16/groth16_snark.hpp"
#include "serialization/proto_utils.hpp"
#include "serialization/r1cs_variable_assignment_serialization.hpp"

#include <boost/filesystem.hpp>
#include <gtest/gtest.h>
#include <libff/algebra/curves/alt_bn128/alt_bn128_pp.hpp>

using namespace libsnark;
using namespace libzeth;

using pp = libff::alt_bn128_pp;
using Field = libff::Fr<pp>;

boost::filesystem::path g_output_dir = boost::filesystem::path("");

namespace
{

TEST(SimpleTests, SimpleCircuitProof)
{
    // Simple circuit
    protoboard<Field> pb;
    libzeth::tests::simple_circuit<Field>(pb);

    // Constraint system
    const r1cs_constraint_system<Field> &constraint_system =
        pb.get_constraint_system();

    // Write to file if output directory is given.
    if (!g_output_dir.empty()) {

        boost::filesystem::path outpath =
            g_output_dir / ("simple_circuit_r1cs_" + pp_name<pp>() + ".json");
        std::ofstream r1cs_stream(outpath.c_str());
        libzeth::r1cs_write_json(pb.get_constraint_system(), r1cs_stream);
    }

    const r1cs_primary_input<Field> primary{12};
    const r1cs_auxiliary_input<Field> auxiliary{1, 1, 1};

    {
        // Test solution x = 1 (g1 = 1, g2 = 1), y = 12
        ASSERT_TRUE(constraint_system.is_satisfied(primary, auxiliary));

        const r1cs_auxiliary_input<Field> auxiliary_invalid[]{
            r1cs_auxiliary_input<Field>{2, 1, 2},
            r1cs_auxiliary_input<Field>{1, 2, 2},
            r1cs_auxiliary_input<Field>{1, 1, 2},
        };
        for (const auto &invalid : auxiliary_invalid) {
            ASSERT_FALSE(constraint_system.is_satisfied(primary, invalid));
        }
    }

    const r1cs_gg_ppzksnark_keypair<pp> keypair =
        r1cs_gg_ppzksnark_generator<pp>(constraint_system);

    const r1cs_gg_ppzksnark_proof<pp> proof =
        r1cs_gg_ppzksnark_prover(keypair.pk, primary, auxiliary);

    ASSERT_TRUE(
        r1cs_gg_ppzksnark_verifier_strong_IC(keypair.vk, primary, proof));

    if (!g_output_dir.empty()) {
        {
            boost::filesystem::path proving_key_path =
                g_output_dir / ("simple_proving_key_" + pp_name<pp>() + ".bin");
            std::ofstream out_s(proving_key_path.c_str());
            groth16_snark<pp>::proving_key_write_bytes(keypair.pk, out_s);
        }
        {
            boost::filesystem::path verification_key_path =
                g_output_dir /
                ("simple_verification_key_" + pp_name<pp>() + ".bin");
            std::ofstream out_s(verification_key_path.c_str());
            groth16_snark<pp>::verification_key_write_bytes(keypair.vk, out_s);
        }
        {
            boost::filesystem::path primary_inputs_path =
                g_output_dir /
                ("simple_primary_input_" + pp_name<pp>() + ".bin");
            std::ofstream out_s(primary_inputs_path.c_str());
            r1cs_variable_assignment_write_bytes(primary, out_s);
        }
    }
}

TEST(SimpleTests, SimpleCircuitProofPow2Domain)
{
    // Simple circuit
    protoboard<Field> pb;
    libzeth::tests::simple_circuit<Field>(pb);

    const r1cs_constraint_system<Field> &constraint_system =
        pb.get_constraint_system();
    const r1cs_gg_ppzksnark_keypair<pp> keypair =
        r1cs_gg_ppzksnark_generator<pp>(constraint_system, true);

    const r1cs_primary_input<Field> primary{12};
    const r1cs_auxiliary_input<Field> auxiliary{1, 1, 1};
    const r1cs_gg_ppzksnark_proof<pp> proof =
        r1cs_gg_ppzksnark_prover(keypair.pk, primary, auxiliary, true);
    ASSERT_TRUE(
        r1cs_gg_ppzksnark_verifier_strong_IC(keypair.vk, primary, proof));
}

} // namespace

int main(int argc, char **argv)
{
    // WARNING: Do once for all tests. Do not forget to do this.
    pp::init_public_params();

    // Remove stdout noise from libff
    libff::inhibit_profiling_counters = true;
    libff::inhibit_profiling_info = true;
    ::testing::InitGoogleTest(&argc, argv);

    // Extract the test data destination dir, if passed on the command line.
    if (argc > 1) {
        g_output_dir = boost::filesystem::path(argv[1]);
    }

    return RUN_ALL_TESTS();
}
