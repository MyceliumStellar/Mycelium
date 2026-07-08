/**
 * Shared Stellar network configuration — the single source of truth for the
 * IDE frontend. All pages import from here instead of hardcoding addresses.
 *
 * Mainnet contract IDs are "__PENDING__" until manually deployed.
 * After deploying, update the `mainnet` entries below.
 */

export type NetworkType = "testnet" | "mainnet";

export const SOROBAN_RPC_URLS: Record<NetworkType, string> = {
  testnet: "https://soroban-testnet.stellar.org",
  mainnet: "https://mainnet.sorobanrpc.com",
};

export const HORIZON_URLS: Record<NetworkType, string> = {
  testnet: "https://horizon-testnet.stellar.org",
  mainnet: "https://horizon.stellar.org",
};

export const STELLAR_EXPERT_URLS: Record<NetworkType, string> = {
  testnet: "https://stellar.expert/explorer/testnet",
  mainnet: "https://stellar.expert/explorer/public",
};

export const NETWORK_PASSPHRASES: Record<NetworkType, string> = {
  testnet: "Test SDF Network ; September 2015",
  mainnet: "Public Global Stellar Network ; September 2015",
};

export const CONTRACT_ADDRESSES: Record<NetworkType, {
  hive_registry: string;
  job_board: string;
  memory_anchor: string;
  verifier_registry: string;
  reputation_registry: string;
}> = {
  testnet: {
    hive_registry:       "CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC",
    job_board:           "CDASJ42STDU42QXDXH3KRFNQWBURB54XPXV2WBXHWGPBA2BNAI5EYULO",
    memory_anchor:       "CAC27VKJEPDJJNI36NP7D7VH6WCHT6N5EITKSKPZIQNWA2VPEPBIXJSB",
    verifier_registry:   "CBFELTFVBRGR5Y4VHOGFUJLNMMRDNBAOTTZUKZ3SNT625GDB4T76OHMC",
    reputation_registry: "CCTJCC5FELB4PSXT3OF4QSFKH456OIVHF3YGY7ABNFH7ITL7XWYBO2NE",
  },
  mainnet: {
    hive_registry:       "CCFGTAAVOCU2VQNNQUJQQI3YET27PTM3GADCBYDLA6DISXUPR5CGRS5T",
    job_board:           "CABB4SSGE5NFOCH6KE4RNCA2MGHSQIFXUKS7OZ4B4GQOEJK6R4ZMP4LG",
    memory_anchor:       "CDFXP42NITRLDGYUMJ5OT63EVWBROJTCXQR64GUSDWHY2LH3AQM2TXYP",
    verifier_registry:   "CA574F2GDVGJSITE52TFON7MA66HB6EC2IVPMXPO5OUWDAPJ5JVCSQHC",
    reputation_registry: "CB44VUD27BJN4R2VVUONP63TQ5LG523XPV4TKFF7CLC3MQBHI7DYKRBP",
  },
};

export const NATIVE_SAC_ADDRESSES: Record<NetworkType, string> = {
  testnet: "CDLZFC3SYJYDZT7K67VZ75HPJVIEUVNIXF47ZG2FB2RMQQVU2HHGCYSC",
  mainnet: "CAS3J7GYLGXMF6TDJBBYYSE3HQ6BBSMLNUQ34T6TZMYMW2EVH34XOWMA",
};

/**
 * Detect the network from the Freighter wallet's reported network string.
 * Freighter reports "TESTNET" or "PUBLIC"; we normalize to our type.
 */
export function detectNetwork(freighterNetwork: string): NetworkType {
  return freighterNetwork === "PUBLIC" ? "mainnet" : "testnet";
}

/**
 * Get the StellarSdk.Networks constant name for a given network.
 */
export function sdkNetworkPassphrase(network: NetworkType): string {
  return NETWORK_PASSPHRASES[network];
}
