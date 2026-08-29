"""
NEWFLOW Core v0.3
Consensus: Proof of Verifiable Compute (PoVC)

The Distributed AI Hypothesis:
  Any computer in the world can contribute compute to AI.
  No permission required. No central owner.
  Energy flows in. Value flows out. The cycle continues.

  Energy -> Compute -> Proof -> Value -> New Energy
     ^                                       |
     |_______________ NEWFLOW _______________|

  Payment is inseparable from proof.
  Verifying proof = settling payment. One step. One call.
  No escrow. No multisig. No smart contract. No judge.

  "Energy should not need permission from any central bank."

Dependencies: pip install cryptography
"""

import hashlib
import json
import math
import time
import unittest
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat, PrivateFormat, NoEncryption
)


# ══════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════

VERSION          = "NEWFLOW v0.3"
TICKER           = "ECU"
MAX_SUPPLY       = 950_000
FOUNDER_AMOUNT   = 9_500
NETWORK_RESERVE  = 855_000
BASE_REWARD      = 0.0733     # yields 95-year mine schedule | 10-min blocks | avg 200 CU/block
BLOCK_TIME_MIN   = 10         # minutes — mirrors Bitcoin
HALVING_INTERVAL = MAX_SUPPLY // 4  # 237,500 ECU per halving phase
ADDRESS_PREFIX   = 0x4E       # N — NEWFLOW

REWARD_SPLIT = {
    "workers":   0.70,
    "validator": 0.20,
    "treasury":  0.10,
}

# Faucet — wallet #10 distributes 10 ECU to first 950 registered users
FAUCET_AMOUNT    = 10
FAUCET_MAX_USERS = 950
FAUCET_TOTAL     = 9_500   # = FAUCET_AMOUNT * FAUCET_MAX_USERS


# ══════════════════════════════════════════════════════════
# HARDWARE TIERS & COMPUTE UNIT DEFINITIONS
#
# The Distributed AI Hypothesis:
#   Every machine — from a gaming laptop to a server cluster —
#   can participate as a worker. CU definitions map diverse
#   hardware capabilities to a common unit of account.
#
# Hardware tiers:
#   EDGE   : smartphones, Raspberry Pi, tiny models
#   LIGHT  : laptops, CPU servers, small GPU
#   MEDIUM : gaming GPU (RTX 3080+), workstations
#   HEAVY  : multi-GPU rigs, professional accelerators
#   CLUSTER: datacenter-scale, 8+ GPU nodes
# ══════════════════════════════════════════════════════════

class HardwareTier(Enum):
    EDGE    = "edge"
    LIGHT   = "light"
    MEDIUM  = "medium"
    HEAVY   = "heavy"
    CLUSTER = "cluster"


# Task type -> (CU per unit, minimum hardware tier required)
COMPUTE_UNIT_DEFINITIONS: dict[str, tuple[int, HardwareTier]] = {
    # AI inference
    "llm_inference_tiny_1B_tokens":   (5,   HardwareTier.EDGE),    # <1B param models, edge devices
    "llm_inference_small_1B_tokens":  (20,  HardwareTier.LIGHT),   # 1-7B param models
    "llm_inference_mid_1B_tokens":    (100, HardwareTier.MEDIUM),  # 7-70B param models
    "llm_inference_large_1B_tokens":  (400, HardwareTier.HEAVY),   # 70B+ param models
    # AI training
    "llm_finetune_1M_tokens":         (50,  HardwareTier.MEDIUM),
    "llm_pretrain_1M_tokens":         (500, HardwareTier.CLUSTER),
    # Embeddings & preprocessing
    "embedding_1M_tokens":            (2,   HardwareTier.EDGE),
    "tokenize_1M_tokens":             (1,   HardwareTier.EDGE),
    # Image & video AI
    "image_generation_512px":         (10,  HardwareTier.LIGHT),
    "image_generation_4k":            (80,  HardwareTier.HEAVY),
    "video_diffusion_1s_720p":        (200, HardwareTier.HEAVY),
    # Scientific compute
    "matrix_mul_1M_ops":              (1,   HardwareTier.EDGE),
    "genome_seq_1M_reads":            (75,  HardwareTier.MEDIUM),
    "molecular_sim_1M_steps":         (150, HardwareTier.HEAVY),
    # Rendering
    "render_frame_4k":                (50,  HardwareTier.MEDIUM),
    "video_transcode_1min_4k":        (200, HardwareTier.HEAVY),
}

# Legacy shorthand for backward compatibility
TASK_CU = {k: v[0] for k, v in COMPUTE_UNIT_DEFINITIONS.items()}
TASK_MIN_TIER = {k: v[1] for k, v in COMPUTE_UNIT_DEFINITIONS.items()}


# ══════════════════════════════════════════════════════════
# WORKER PROFILE
# A registered participant contributing compute to the network.
# Any machine can register — no permission required.
# ══════════════════════════════════════════════════════════

@dataclass
class WorkerProfile:
    """
    Hardware declaration submitted by a worker on registration.
    Used by the job marketplace to match jobs to capable workers.
    Workers self-declare hardware; validators may challenge
    via benchmark proofs in future versions.
    """
    worker_address:  str
    worker_pubkey:   str          # hex Ed25519
    hardware_tier:   HardwareTier
    gpu_model:       str          # e.g. "RTX 4090", "M2 Ultra", "CPU-only"
    ram_gb:          int
    supported_tasks: list[str]    # subset of COMPUTE_UNIT_DEFINITIONS keys
    registered_at:   int          # unix timestamp
    signature:       str = ""     # worker signs their own profile

    def signing_payload(self) -> bytes:
        return json.dumps({
            "worker_address":  self.worker_address,
            "hardware_tier":   self.hardware_tier.value,
            "gpu_model":       self.gpu_model,
            "ram_gb":          self.ram_gb,
            "supported_tasks": sorted(self.supported_tasks),
            "registered_at":   self.registered_at,
        }, separators=(",", ":"), sort_keys=True).encode()

    def sign(self, wallet: "Wallet") -> None:
        self.signature = wallet.sign(self.signing_payload()).hex()

    def verify(self) -> bool:
        return _verify_sig(
            bytes.fromhex(self.worker_pubkey),
            self.signing_payload(),
            bytes.fromhex(self.signature),
        )

    def can_handle(self, task_type: str) -> bool:
        """Check if this worker can handle a given task type."""
        if task_type not in COMPUTE_UNIT_DEFINITIONS:
            return False
        required_tier = TASK_MIN_TIER[task_type]
        tier_order = [t for t in HardwareTier]
        return (
            task_type in self.supported_tasks and
            tier_order.index(self.hardware_tier) >= tier_order.index(required_tier)
        )


# ══════════════════════════════════════════════════════════
# BASE58CHECK
# ══════════════════════════════════════════════════════════

_BASE58_ALPHA = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(data: bytes) -> str:
    leading = len(data) - len(data.lstrip(b"\x00"))
    num = int.from_bytes(data, "big")
    result = []
    while num > 0:
        num, r = divmod(num, 58)
        result.append(_BASE58_ALPHA[r:r+1])
    result.extend([_BASE58_ALPHA[0:1]] * leading)
    return b"".join(reversed(result)).decode()


def b58decode(s: str) -> bytes:
    leading = len(s) - len(s.lstrip("1"))
    num = 0
    for ch in s.encode():
        num = num * 58 + _BASE58_ALPHA.index(ch)
    result = num.to_bytes((num.bit_length() + 7) // 8 or 1, "big")
    return b"\x00" * leading + result


def address_encode(public_key_bytes: bytes) -> str:
    """Derive a NEWFLOW address from an Ed25519 public key. Prefix: 0x4E (N)."""
    pk_hash   = hashlib.sha256(public_key_bytes).digest()
    versioned = bytes([ADDRESS_PREFIX]) + pk_hash
    checksum  = hashlib.sha256(hashlib.sha256(versioned).digest()).digest()[:4]
    return b58encode(versioned + checksum)


def address_verify(address: str) -> bool:
    """Verify the checksum of a NEWFLOW address."""
    try:
        raw      = b58decode(address)
        payload  = raw[:-4]
        checksum = raw[-4:]
        expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
        return checksum == expected and payload[0] == ADDRESS_PREFIX
    except Exception:
        return False


# ══════════════════════════════════════════════════════════
# ED25519 WALLET
# ══════════════════════════════════════════════════════════

class Wallet:
    def __init__(self, private_key_hex: Optional[str] = None):
        if private_key_hex:
            self._priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
        else:
            self._priv = Ed25519PrivateKey.generate()
        self._pub       = self._priv.public_key()
        self.public_key = self._pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.address    = address_encode(self.public_key)

    @property
    def private_key_hex(self) -> str:
        return self._priv.private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()
        ).hex()

    def sign(self, message: bytes) -> bytes:
        return self._priv.sign(message)

    def verify(self, message: bytes, signature: bytes) -> bool:
        try:
            self._pub.verify(signature, message)
            return True
        except Exception:
            return False

    def __repr__(self):
        return f"Wallet({self.address[:20]}...)"


def _verify_sig(public_key_bytes: bytes, message: bytes, signature: bytes) -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(signature, message)
        return True
    except Exception:
        return False


def sha256(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


# ══════════════════════════════════════════════════════════
# PROOF-CARRIES-PAYMENT
#
# Core mechanism:
#   1. Consumer creates JobCommitment: job spec + fee + worker address -> sign
#   2. Worker executes job, generates zkSNARK proof -> ProofWithPayment -> sign
#   3. Validator calls verify_all(): proof + payment auth + worker sig — one call
#   4. Pass -> payment settles in block. No separate transaction needed.
# ══════════════════════════════════════════════════════════

@dataclass
class JobCommitment:
    """
    Created and signed by the consumer.
    Embeds payment authorization into the job request.
    Cannot be separated from the proof.
    """
    job_id:             str
    task_type:          str
    input_hash:         str
    compute_units:      int
    fee:                int
    consumer_address:   str
    consumer_pubkey:    str
    worker_address:     str
    timestamp:          int
    consumer_signature: str = ""

    def signing_payload(self) -> bytes:
        return json.dumps({
            "job_id":           self.job_id,
            "task_type":        self.task_type,
            "input_hash":       self.input_hash,
            "compute_units":    self.compute_units,
            "fee":              self.fee,
            "consumer_address": self.consumer_address,
            "worker_address":   self.worker_address,
            "timestamp":        self.timestamp,
        }, separators=(",", ":"), sort_keys=True).encode()

    def sign(self, wallet: Wallet) -> None:
        self.consumer_signature = wallet.sign(self.signing_payload()).hex()

    def verify(self) -> bool:
        return _verify_sig(
            bytes.fromhex(self.consumer_pubkey),
            self.signing_payload(),
            bytes.fromhex(self.consumer_signature),
        )


@dataclass
class ProofWithPayment:
    """
    Created and signed by the worker after completing the job.
    Proof and payment authorization are one inseparable artifact.
    Verifying the proof automatically authorizes the payment.
    """
    job_id:           str
    input_hash:       str
    output_hash:      str
    proof:            str   # zkSNARK proof — PLONK/BN254 on mainnet, mock on testnet
    compute_units:    int
    fee:              int   # must match JobCommitment.fee exactly
    worker_address:   str
    worker_pubkey:    str
    job_commitment:   JobCommitment
    worker_signature: str = ""

    def signing_payload(self) -> bytes:
        return json.dumps({
            "job_id":             self.job_id,
            "output_hash":        self.output_hash,
            "proof":              self.proof,
            "compute_units":      self.compute_units,
            "fee":                self.fee,
            "worker_address":     self.worker_address,
            "consumer_signature": self.job_commitment.consumer_signature,
        }, separators=(",", ":"), sort_keys=True).encode()

    def sign(self, wallet: Wallet) -> None:
        self.worker_signature = wallet.sign(self.signing_payload()).hex()

    def verify_proof(self) -> bool:
        """
        Testnet: deterministic mock.
        Mainnet: PLONK verify(circuit, public_inputs, proof)
        """
        expected = sha256(
            f"{self.input_hash}:{self.job_commitment.task_type}:{self.worker_address}"
        )
        return self.proof == expected

    def verify_payment_authorization(self) -> bool:
        """Verify consumer authorized payment to the correct worker for the correct fee."""
        return (
            self.job_commitment.verify() and
            self.job_commitment.worker_address == self.worker_address and
            self.job_commitment.fee == self.fee and
            self.job_commitment.job_id == self.job_id
        )

    def verify_worker_signature(self) -> bool:
        return _verify_sig(
            bytes.fromhex(self.worker_pubkey),
            self.signing_payload(),
            bytes.fromhex(self.worker_signature),
        )

    def verify_all(self) -> tuple[bool, str]:
        """
        Called by the validator — one call, three checks simultaneously.
        Cannot pass proof without payment, cannot pass payment without proof.
        """
        if not self.verify_proof():
            return False, "Invalid proof"
        if not self.verify_payment_authorization():
            return False, "Invalid payment authorization"
        if not self.verify_worker_signature():
            return False, "Invalid worker signature"
        return True, "OK"


def generate_mock_proof(input_hash: str, task_type: str, worker_address: str) -> str:
    """Testnet only — generates a deterministic mock proof for testing the full flow."""
    return sha256(f"{input_hash}:{task_type}:{worker_address}")


# ══════════════════════════════════════════════════════════
# TRANSFER TRANSACTION
# ══════════════════════════════════════════════════════════

@dataclass
class Transfer:
    """
    Free ECU transfer between two addresses.
    Nonce increments per sender to prevent replay attacks.
    """
    sender:        str
    sender_pubkey: str
    receiver:      str
    amount:        int
    fee:           int   # paid to validator, may be 0
    nonce:         int   # must match sender\'s current nonce exactly
    timestamp:     int
    signature:     str = ""

    def tx_id(self) -> str:
        return sha256(f"{self.sender}:{self.receiver}:{self.amount}:{self.nonce}:{self.timestamp}")

    def signing_payload(self) -> bytes:
        return json.dumps({
            "sender":    self.sender,
            "receiver":  self.receiver,
            "amount":    self.amount,
            "fee":       self.fee,
            "nonce":     self.nonce,
            "timestamp": self.timestamp,
        }, separators=(",", ":"), sort_keys=True).encode()

    def sign(self, wallet: Wallet) -> None:
        self.signature = wallet.sign(self.signing_payload()).hex()

    def verify(self) -> bool:
        return _verify_sig(
            bytes.fromhex(self.sender_pubkey),
            self.signing_payload(),
            bytes.fromhex(self.signature),
        )


# ══════════════════════════════════════════════════════════
# FAUCET
# Wallet #10 distributes 10 ECU to the first 950 registered users.
# Bootstraps the marketplace by giving new participants initial ECU.
# Total: 9,500 ECU / 10 ECU = 950 users maximum.
# ══════════════════════════════════════════════════════════

@dataclass
class FaucetClaim:
    """Special transfer from the faucet wallet — no fee charged."""
    receiver:        str
    receiver_pubkey: str
    timestamp:       int
    signature:       str = ""

    def signing_payload(self) -> bytes:
        return json.dumps({
            "receiver":  self.receiver,
            "faucet":    "NEWFLOW_FAUCET_V1",
            "amount":    FAUCET_AMOUNT,
            "timestamp": self.timestamp,
        }, separators=(",", ":"), sort_keys=True).encode()

    def sign(self, wallet: Wallet) -> None:
        self.signature = wallet.sign(self.signing_payload()).hex()

    def verify(self) -> bool:
        return _verify_sig(
            bytes.fromhex(self.receiver_pubkey),
            self.signing_payload(),
            bytes.fromhex(self.signature),
        )


# ══════════════════════════════════════════════════════════
# SETTLEMENT & BLOCK
# ══════════════════════════════════════════════════════════

@dataclass
class Settlement:
    """
    Result of a verified job settled within a block.
    Not a transaction — an integral part of block construction.
    """
    job_id:           str
    worker_address:   str
    consumer_address: str
    amount:           int
    compute_units:    int


@dataclass
class Block:
    index:               int
    version:             str
    timestamp:           int
    previous_hash:       str
    proofs:              list[ProofWithPayment] = field(default_factory=list)
    settlements:         list[Settlement]       = field(default_factory=list)
    jobs_root:           str = "0" * 64
    total_compute_units: int = 0
    total_settled:       int = 0
    validator_address:   str = ""
    validator_signature: str = ""
    hash:                str = ""


def build_merkle_root(proofs: list[ProofWithPayment]) -> str:
    if not proofs:
        return "0" * 64
    leaves = [
        sha256(f"{p.job_id}:{p.output_hash}:{p.proof}:{p.worker_address}")
        for p in proofs
    ]
    while len(leaves) > 1:
        if len(leaves) % 2 == 1:
            leaves.append(leaves[-1])
        leaves = [sha256(leaves[i] + leaves[i+1]) for i in range(0, len(leaves), 2)]
    return leaves[0]


def compute_block_hash(block: Block) -> str:
    """Canonical hash — immutable after genesis. Any change = fork."""
    canonical = {
        "index":               block.index,
        "jobs_root":           block.jobs_root,
        "previous_hash":       block.previous_hash,
        "timestamp":           block.timestamp,
        "total_compute_units": block.total_compute_units,
        "validator_address":   block.validator_address,
        "version":             block.version,
    }
    return sha256(json.dumps(canonical, separators=(",", ":"), sort_keys=True))


def build_block(index: int,
                previous_hash: str,
                proofs: list[ProofWithPayment],
                validator_wallet: Wallet) -> Optional[Block]:
    valid_proofs  = []
    settlements   = []
    total_cu      = 0
    total_settled = 0

    for p in proofs:
        ok, reason = p.verify_all()
        if not ok:
            print(f"  [SKIP] job={p.job_id[:8]}... reason={reason}")
            continue
        valid_proofs.append(p)
        settlements.append(Settlement(
            job_id           = p.job_id,
            worker_address   = p.worker_address,
            consumer_address = p.job_commitment.consumer_address,
            amount           = p.fee,
            compute_units    = p.compute_units,
        ))
        total_cu      += p.compute_units
        total_settled += p.fee

    if not valid_proofs:
        return None

    block = Block(
        index               = index,
        version             = VERSION,
        timestamp           = int(time.time()),
        previous_hash       = previous_hash,
        proofs              = valid_proofs,
        settlements         = settlements,
        jobs_root           = build_merkle_root(valid_proofs),
        total_compute_units = total_cu,
        total_settled       = total_settled,
        validator_address   = validator_wallet.address,
    )
    block.hash = compute_block_hash(block)
    block.validator_signature = validator_wallet.sign(block.hash.encode()).hex()
    return block


# ══════════════════════════════════════════════════════════
# VALIDATION ERROR
# ══════════════════════════════════════════════════════════

@dataclass
class ValidationError(Exception):
    reason: str
    def __str__(self): return f"ValidationError: {self.reason}"


# ══════════════════════════════════════════════════════════
# CHAIN STATE
# Single source of truth for the entire chain.
# Everything derives from here: balances, spent jobs, chain tip.
# ══════════════════════════════════════════════════════════

@dataclass
class ChainState:
    blocks:          list[Block]    = field(default_factory=list)
    balances:        dict[str, int] = field(default_factory=dict)
    spent_jobs:      set[str]       = field(default_factory=set)
    nonces:          dict[str, int] = field(default_factory=dict)
    faucet_claimed:  set[str]       = field(default_factory=set)
    worker_profiles: dict[str, WorkerProfile] = field(default_factory=dict)
    faucet_address:  str            = ""
    height:          int            = -1
    tip_hash:        str            = "0" * 64

    # ── GENESIS ──────────────────────────────────────────

    def apply_genesis(self, allocations: dict[str, int], faucet_address: str = "") -> None:
        """Load genesis allocations into balances. Call only once."""
        if self.height >= 0:
            raise ValidationError("Genesis already applied")
        for address, amount in allocations.items():
            if address == "NETWORK_RESERVE":
                # Whitepaper §4: the Network Reserve is "released via verified
                # compute — 95 years", and §3 is explicit that there are "no
                # meaningless block rewards". The old comment here said the
                # opposite ("released only via block rewards"), which named a
                # mechanism the protocol does not have. Skipping is right
                # either way — the reserve is not a balance anyone holds at
                # genesis — but the reason had to match the paper.
                #
                # No release path is implemented yet, by verified compute or
                # anything else. Nothing has ever come out of this reserve.
                continue
            self.balances[address] = amount
        self.faucet_address = faucet_address
        self.height   = 0
        self.tip_hash = "0" * 64

    # ── WORKER REGISTRATION ───────────────────────────────

    def register_worker(self, profile: WorkerProfile) -> None:
        """
        Any machine can register as a worker — no permission required.
        Worker self-declares hardware tier and supported task types.
        """
        if not profile.verify():
            raise ValidationError("Invalid worker profile signature")
        for task in profile.supported_tasks:
            if task not in COMPUTE_UNIT_DEFINITIONS:
                raise ValidationError(f"Unknown task type: {task}")
            required = TASK_MIN_TIER[task]
            tier_order = list(HardwareTier)
            if tier_order.index(profile.hardware_tier) < tier_order.index(required):
                raise ValidationError(
                    f"Hardware tier {profile.hardware_tier.value} insufficient for {task} "
                    f"(requires {required.value})"
                )
        self.worker_profiles[profile.worker_address] = profile

    def get_eligible_workers(self, task_type: str) -> list[WorkerProfile]:
        """Return all registered workers capable of handling a given task."""
        return [p for p in self.worker_profiles.values() if p.can_handle(task_type)]

    # ── VALIDATION ───────────────────────────────────────

    def validate_block(self, block: Block) -> None:
        if block.index != self.height + 1:
            raise ValidationError(
                f"Invalid index: expected {self.height+1}, got {block.index}"
            )
        if block.previous_hash != self.tip_hash:
            raise ValidationError("previous_hash does not match chain tip")
        if compute_block_hash(block) != block.hash:
            raise ValidationError("Invalid block hash")
        if not block.settlements:
            raise ValidationError("Empty block — no settlements")
        for s in block.settlements:
            if s.job_id in self.spent_jobs:
                raise ValidationError(f"Double-spend: job_id {s.job_id[:16]}... already settled")
            if self.balances.get(s.consumer_address, 0) < s.amount:
                raise ValidationError(
                    f"Insufficient balance: {s.consumer_address[:20]}... "
                    f"has {self.balances.get(s.consumer_address, 0)} ECU, needs {s.amount} ECU"
                )
        if build_merkle_root(block.proofs) != block.jobs_root:
            raise ValidationError("jobs_root does not match Merkle root of proofs")

    # ── APPLY BLOCK ───────────────────────────────────────

    def apply_block(self, block: Block) -> None:
        """
        Validate then apply block to state.
        Atomic: if validation fails, state is not modified.
        """
        self.validate_block(block)
        for s in block.settlements:
            self.balances[s.consumer_address] = self.balances.get(s.consumer_address, 0) - s.amount
            self.balances[s.worker_address]   = self.balances.get(s.worker_address, 0) + s.amount
            self.spent_jobs.add(s.job_id)
        self.blocks.append(block)
        self.height   = block.index
        self.tip_hash = block.hash

    # ── ROLLBACK ─────────────────────────────────────────

    def rollback(self, to_height: int) -> None:
        """Rollback chain to given height. Used for fork resolution."""
        if to_height >= self.height:
            raise ValidationError("Cannot rollback to a height higher than current")
        if to_height < 0:
            raise ValidationError("Cannot rollback before genesis")
        while self.height > to_height:
            block = self.blocks.pop()
            for s in reversed(block.settlements):
                self.balances[s.consumer_address] = self.balances.get(s.consumer_address, 0) + s.amount
                self.balances[s.worker_address]   = self.balances.get(s.worker_address, 0) - s.amount
                self.spent_jobs.discard(s.job_id)
            self.height   = block.index - 1
            self.tip_hash = block.previous_hash

    # ── TRANSFER ─────────────────────────────────────────

    def validate_transfer(self, tx: Transfer) -> None:
        if not tx.verify():
            raise ValidationError("Invalid transfer signature")
        expected = self.nonces.get(tx.sender, 0)
        if tx.nonce != expected:
            raise ValidationError(f"Invalid nonce: expected {expected}, got {tx.nonce}")
        if tx.amount <= 0:
            raise ValidationError("Amount must be > 0")
        if tx.receiver == tx.sender:
            raise ValidationError("Cannot send to self")
        total = tx.amount + tx.fee
        if self.balances.get(tx.sender, 0) < total:
            raise ValidationError(
                f"Insufficient balance: {tx.sender[:20]}... "
                f"has {self.balances.get(tx.sender, 0)} ECU, needs {total} ECU"
            )

    def apply_transfer(self, tx: Transfer, validator_address: str) -> None:
        self.validate_transfer(tx)
        self.balances[tx.sender]   = self.balances.get(tx.sender, 0) - tx.amount - tx.fee
        self.balances[tx.receiver] = self.balances.get(tx.receiver, 0) + tx.amount
        if tx.fee > 0:
            self.balances[validator_address] = self.balances.get(validator_address, 0) + tx.fee
        self.nonces[tx.sender] = tx.nonce + 1

    # ── FAUCET ───────────────────────────────────────────

    def validate_faucet_claim(self, claim: FaucetClaim) -> None:
        if claim.receiver in self.faucet_claimed:
            raise ValidationError("Address has already claimed from faucet")
        if len(self.faucet_claimed) >= FAUCET_MAX_USERS:
            raise ValidationError(f"Faucet exhausted — {FAUCET_MAX_USERS} users claimed")
        if self.balances.get(self.faucet_address, 0) < FAUCET_AMOUNT:
            raise ValidationError("Faucet wallet is empty")
        if not claim.verify():
            raise ValidationError("Invalid faucet claim signature")

    def apply_faucet_claim(self, claim: FaucetClaim) -> None:
        self.validate_faucet_claim(claim)
        self.balances[self.faucet_address] = self.balances.get(self.faucet_address, 0) - FAUCET_AMOUNT
        self.balances[claim.receiver]      = self.balances.get(claim.receiver, 0) + FAUCET_AMOUNT
        self.faucet_claimed.add(claim.receiver)

    # ── QUERY ────────────────────────────────────────────

    def get_balance(self, address: str) -> int:
        return self.balances.get(address, 0)

    def get_nonce(self, address: str) -> int:
        return self.nonces.get(address, 0)

    def is_spent(self, job_id: str) -> bool:
        return job_id in self.spent_jobs

    def faucet_slots_remaining(self) -> int:
        return FAUCET_MAX_USERS - len(self.faucet_claimed)

    def snapshot(self) -> dict:
        """Current state snapshot for debugging or persistence."""
        return {
            "height":     self.height,
            "tip_hash":   self.tip_hash,
            "balances":   dict(self.balances),
            "spent_jobs": list(self.spent_jobs),
            "workers":    len(self.worker_profiles),
            "faucet_remaining": self.faucet_slots_remaining(),
        }


# ══════════════════════════════════════════════════════════
# TOKENOMICS
# ══════════════════════════════════════════════════════════

def calculate_block_reward(total_compute_units: int, current_supply: int) -> float:
    """
    BASE_REWARD = 0.0733 -> 95-year mine schedule
    Block time: 10 min | Avg CU/block: 200 | Empty block -> 0 reward
    """
    if current_supply >= MAX_SUPPLY or total_compute_units == 0:
        return 0.0
    reward   = BASE_REWARD * math.log2(total_compute_units + 1)
    halvings = current_supply // HALVING_INTERVAL
    reward   = reward / (2 ** halvings)
    return min(reward, MAX_SUPPLY - current_supply)


def distribute_reward(reward: float, jobs: list[ProofWithPayment]) -> dict:
    """Distribute block reward: 70% workers by CU, 20% validator, 10% treasury."""
    total_cu    = sum(j.compute_units for j in jobs)
    worker_pool = reward * REWARD_SPLIT["workers"]
    dist = {}
    for job in jobs:
        share = (job.compute_units / total_cu) * worker_pool if total_cu else 0
        dist[job.worker_address] = dist.get(job.worker_address, 0) + share
    return dist


# ══════════════════════════════════════════════════════════
# UNIT TESTS
# ══════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════
# LEDGER
# ══════════════════════════════════════════════════════════

class EntryType(Enum):
    GENESIS              = "genesis"
    TRANSFER             = "transfer"
    JOB_SETTLEMENT       = "job_settlement"
    BLOCK_REWARD         = "block_reward"
    FAUCET               = "faucet"
    PROOF_PENDING        = "proof_pending"
    PROOF_APPROVED       = "proof_approved"
    PROOF_SETTLED        = "proof_settled"
    PROOF_SLASHED        = "proof_slashed"


@dataclass
class LedgerEntry:
    entry_type: EntryType
    from_addr:  str
    to_addr:    str
    amount:     int
    block:      int
    timestamp:  int
    ref_id:     str
    metadata:   dict = field(default_factory=dict)

    def entry_id(self) -> str:
        return sha256(f"{self.entry_type.value}:{self.from_addr}:{self.to_addr}:{self.amount}:{self.block}:{self.ref_id}")

    def to_dict(self) -> dict:
        return {"entry_id": self.entry_id(), "entry_type": self.entry_type.value,
                "from": self.from_addr, "to": self.to_addr, "amount": self.amount,
                "block": self.block, "timestamp": self.timestamp,
                "ref_id": self.ref_id, "metadata": self.metadata}


@dataclass
class Ledger:
    _entries:          list[LedgerEntry]    = field(default_factory=list)
    _index_by_address: dict[str, list[int]] = field(default_factory=dict)
    _index_by_block:   dict[int, list[int]] = field(default_factory=dict)

    def record(self, entry: LedgerEntry) -> None:
        idx = len(self._entries)
        self._entries.append(entry)
        for addr in {entry.from_addr, entry.to_addr}:
            self._index_by_address.setdefault(addr, []).append(idx)
        self._index_by_block.setdefault(entry.block, []).append(idx)

    def history(self, address: str) -> list[LedgerEntry]:
        return [self._entries[i] for i in self._index_by_address.get(address, [])]

    def incoming(self, address: str) -> list[LedgerEntry]:
        return [e for e in self.history(address) if e.to_addr == address]

    def outgoing(self, address: str) -> list[LedgerEntry]:
        return [e for e in self.history(address) if e.from_addr == address]

    def total_received(self, address: str) -> int:
        return sum(e.amount for e in self.incoming(address))

    def total_sent(self, address: str) -> int:
        return sum(e.amount for e in self.outgoing(address))

    def jobs_as_consumer(self, address: str) -> list[LedgerEntry]:
        return [e for e in self.outgoing(address) if e.entry_type == EntryType.JOB_SETTLEMENT]

    def jobs_as_worker(self, address: str) -> list[LedgerEntry]:
        return [e for e in self.incoming(address) if e.entry_type == EntryType.JOB_SETTLEMENT]

    def block_entries(self, block_index: int) -> list[LedgerEntry]:
        return [self._entries[i] for i in self._index_by_block.get(block_index, [])]

    def entries_by_type(self, entry_type: EntryType) -> list[LedgerEntry]:
        return [e for e in self._entries if e.entry_type == entry_type]

    def total_entries(self) -> int:
        return len(self._entries)

    def total_volume(self) -> int:
        return sum(e.amount for e in self._entries if e.entry_type != EntryType.GENESIS)

    def rollback_to_block(self, block_index: int) -> None:
        self._entries = [e for e in self._entries if e.block <= block_index]
        self._index_by_address = {}
        self._index_by_block   = {}
        for idx, entry in enumerate(self._entries):
            for addr in {entry.from_addr, entry.to_addr}:
                self._index_by_address.setdefault(addr, []).append(idx)
            self._index_by_block.setdefault(entry.block, []).append(idx)


    def by_type(self, entry_type: EntryType) -> list[LedgerEntry]:
        return [e for e in self._entries if e.entry_type == entry_type]

    def by_ref(self, ref_id: str) -> list[LedgerEntry]:
        return [e for e in self._entries if e.ref_id == ref_id]

    def pending_proofs(self) -> list[LedgerEntry]:
        submitted = {e.ref_id for e in self.by_type(EntryType.PROOF_PENDING)}
        done = ({e.ref_id for e in self.by_type(EntryType.PROOF_APPROVED)} |
                {e.ref_id for e in self.by_type(EntryType.PROOF_SETTLED)}  |
                {e.ref_id for e in self.by_type(EntryType.PROOF_SLASHED)})
        still = submitted - done
        return [e for e in self.by_type(EntryType.PROOF_PENDING) if e.ref_id in still]

    def proof_status(self, proof_id: str) -> str:
        types = [e.entry_type for e in self.by_ref(proof_id)]
        if not types:                             return "unknown"
        if EntryType.PROOF_SLASHED  in types:    return "slashed"
        if EntryType.PROOF_SETTLED  in types:    return "settled"
        if EntryType.PROOF_APPROVED in types:    return "approved"
        if EntryType.PROOF_PENDING  in types:    return "pending"
        return "unknown"

    def record_proof_pending(self, proof_id, worker, consumer, stake, fee, task, block):
        self.record(LedgerEntry(entry_type=EntryType.PROOF_PENDING,
            from_addr=worker, to_addr="STAKE_LOCK", amount=stake,
            block=block, timestamp=int(time.time()), ref_id=proof_id,
            metadata={"fee": fee, "task": task, "consumer": consumer}))

    def record_proof_approved(self, proof_id, worker, validator, block):
        self.record(LedgerEntry(entry_type=EntryType.PROOF_APPROVED,
            from_addr=validator, to_addr=worker, amount=0,
            block=block, timestamp=int(time.time()), ref_id=proof_id,
            metadata={"validator": validator, "status": "approved"}))

    def record_proof_settled(self, proof_id, worker, consumer, fee, stake, block):
        self.record(LedgerEntry(entry_type=EntryType.PROOF_SETTLED,
            from_addr=consumer, to_addr=worker, amount=fee,
            block=block, timestamp=int(time.time()), ref_id=proof_id,
            metadata={"stake_returned": stake, "status": "settled"}))

    def record_proof_slashed(self, proof_id, worker, validator, stake, block):
        self.record(LedgerEntry(entry_type=EntryType.PROOF_SLASHED,
            from_addr="STAKE_LOCK", to_addr=validator, amount=stake,
            block=block, timestamp=int(time.time()), ref_id=proof_id,
            metadata={"worker": worker, "reason": "invalid_output"}))

    def export(self, address: Optional[str] = None, proof_id: Optional[str] = None) -> list[dict]:
        if proof_id: return [e.to_dict() for e in self.by_ref(proof_id)]
        entries = self.history(address) if address else self._entries
        return [e.to_dict() for e in entries]


# ══════════════════════════════════════════════════════════
# OPTIMISTIC VERIFICATION + STAKING
# ══════════════════════════════════════════════════════════

CHALLENGE_WINDOW_BLOCKS = 24
MIN_STAKE_ECU           = 100
CHALLENGE_REWARD_PCT    = 1.00
STAKE_MULTIPLIER        = 3.0


class ProofStatus(Enum):
    PENDING    = "pending"
    APPROVED   = "approved"   # validator verified OK
    SETTLED    = "settled"
    CHALLENGED = "challenged"
    SLASHED    = "slashed"


@dataclass
class StakedProof:
    job_id: str; task_type: str; input_hash: str; output_hash: str
    compute_units: int; fee: int; stake: int
    worker_address: str; worker_pubkey: str; consumer_address: str
    submitted_block: int; signature: str = ""
    status: ProofStatus = ProofStatus.PENDING

    def proof_id(self) -> str:
        return sha256(f"{self.job_id}:{self.worker_address}:{self.output_hash}:{self.submitted_block}")

    def settlement_block(self) -> int:
        return self.submitted_block + CHALLENGE_WINDOW_BLOCKS

    def is_within_window(self, current_block: int) -> bool:
        return current_block < self.settlement_block()


@dataclass
class Challenge:
    proof_id: str; challenger_address: str; challenger_pubkey: str
    correct_output_hash: str; challenge_stake: int; challenged_block: int
    signature: str = ""; resolved: bool = False; challenger_won: bool = False

    def challenge_id(self) -> str:
        return sha256(f"{self.proof_id}:{self.challenger_address}:{self.challenged_block}")


@dataclass
class OptimisticPool:
    """
    V2: Approval gate prevents fake proofs from settling.
    Proof flow: PENDING -> APPROVED -> SETTLED (or SLASHED).
    process_block() ONLY settles APPROVED proofs.
    """
    pending:    dict[str, StakedProof] = field(default_factory=dict)
    challenges: dict[str, Challenge]   = field(default_factory=dict)
    settled:    list[str]              = field(default_factory=list)
    slashed:    list[str]              = field(default_factory=list)

    def submit_proof(self, proof: StakedProof, balances: dict[str, int]) -> tuple[bool, str]:
        min_stake = int(proof.fee * STAKE_MULTIPLIER)
        if proof.stake < min_stake:
            return False, f"Stake too low: minimum {min_stake} ECU (fee={proof.fee} x {STAKE_MULTIPLIER})"
        if proof.stake < MIN_STAKE_ECU:
            return False, f"Stake below minimum {MIN_STAKE_ECU} ECU"
        if balances.get(proof.worker_address, 0) < proof.stake:
            return False, f"Insufficient balance: need {proof.stake} ECU"
        if balances.get(proof.consumer_address, 0) < proof.fee:
            return False, f"Consumer insufficient balance: need {proof.fee} ECU"
        balances[proof.worker_address] -= proof.stake
        pid = proof.proof_id()
        proof.status = ProofStatus.PENDING
        self.pending[pid] = proof
        return True, f"Proof submitted. stake={proof.stake} ECU | settles block {proof.settlement_block()}"

    def approve_proof(self, proof_id: str) -> bool:
        """Validator explicitly approves a verified proof."""
        proof = self.pending.get(proof_id)
        if proof and proof.status == ProofStatus.PENDING:
            proof.status = ProofStatus.APPROVED
            return True
        return False

    def slash_proof(self, proof_id: str, validator_address: str,
                    validator_stake: int, balances: dict[str, int]) -> tuple[bool, str]:
        """Validator slashes an invalid proof. Validator gets worker stake."""
        proof = self.pending.get(proof_id)
        if not proof:
            return False, "Proof not found"
        proof.status = ProofStatus.SLASHED
        balances[validator_address] = (
            balances.get(validator_address, 0) + validator_stake + proof.stake
        )
        self.slashed.append(proof_id)
        del self.pending[proof_id]
        return True, f"Slashed {proof.stake} ECU from {proof.worker_address[:16]}..."

    def submit_challenge(self, challenge: Challenge,
                         balances: dict[str, int]) -> tuple[bool, str]:
        proof = self.pending.get(challenge.proof_id)
        if not proof:
            return False, "Proof not found or already settled"
        if proof.status not in (ProofStatus.PENDING, ProofStatus.APPROVED):
            return False, f"Proof not challengeable: {proof.status.value}"
        if not proof.is_within_window(challenge.challenged_block):
            return False, "Challenge window has passed"
        if challenge.correct_output_hash == proof.output_hash:
            return False, "Challenge output matches worker output — no dispute"
        if balances.get(challenge.challenger_address, 0) < challenge.challenge_stake:
            return False, "Challenger insufficient balance"
        balances[challenge.challenger_address] -= challenge.challenge_stake
        proof.status = ProofStatus.CHALLENGED
        self.challenges[challenge.challenge_id()] = challenge
        return True, f"Challenge submitted. stake={challenge.challenge_stake} ECU locked"

    def resolve_challenge(self, challenge_id: str, correct_output: str,
                          balances: dict[str, int],
                          treasury_address: str) -> tuple[bool, str]:
        """Challenger gets 100% of worker stake. No treasury cut."""
        challenge = self.challenges.get(challenge_id)
        if not challenge or challenge.resolved:
            return False, "Challenge not found or already resolved"
        proof = self.pending.get(challenge.proof_id)
        if not proof:
            return False, "Associated proof not found"
        challenge.resolved = True
        if correct_output == challenge.correct_output_hash:
            challenge.challenger_won = True
            proof.status = ProofStatus.SLASHED
            balances[challenge.challenger_address] = (
                balances.get(challenge.challenger_address, 0)
                + challenge.challenge_stake + proof.stake
            )
            self.slashed.append(challenge.proof_id)
            del self.pending[challenge.proof_id]
            return True, (f"Challenge WON. Worker slashed {proof.stake} ECU. "
                         f"Challenger received {proof.stake} ECU.")
        else:
            challenge.challenger_won = False
            proof.status = ProofStatus.PENDING
            balances[proof.worker_address] = (
                balances.get(proof.worker_address, 0) + challenge.challenge_stake
            )
            return True, f"Challenge LOST. Challenger slashed {challenge.challenge_stake} ECU."

    def process_block(self, current_block: int,
                      balances: dict[str, int],
                      ledger_records: list) -> list[str]:
        """Settle ONLY APPROVED proofs past their window. NEVER settle PENDING."""
        newly_settled = []
        for pid, proof in list(self.pending.items()):
            if proof.status != ProofStatus.APPROVED:
                continue
            if current_block >= proof.settlement_block():
                proof.status = ProofStatus.SETTLED
                balances[proof.consumer_address] -= proof.fee
                balances[proof.worker_address]   += proof.fee + proof.stake
                ledger_records.append({
                    "type":     "optimistic_settlement",
                    "proof_id": pid,
                    "worker":   proof.worker_address,
                    "consumer": proof.consumer_address,
                    "amount":   proof.fee,
                    "block":    current_block,
                })
                self.settled.append(pid)
                del self.pending[pid]
                newly_settled.append(pid)
        return newly_settled

    def total_staked(self) -> int:
        return sum(p.stake for p in self.pending.values())

    def stats(self) -> dict:
        return {
            "pending":          len(self.pending),
            "approved":         sum(1 for p in self.pending.values() if p.status == ProofStatus.APPROVED),
            "challenged":       sum(1 for p in self.pending.values() if p.status == ProofStatus.CHALLENGED),
            "settled":          len(self.settled),
            "slashed":          len(self.slashed),
            "total_staked_ecu": self.total_staked(),
        }


@dataclass
class ValidatorVerifier:
    """
    Mandatory validator verification before any proof can settle.
    Testnet: deterministic hash check (mock re-execution).
    Mainnet: replace _correct_output() with PLONK zkSNARK verify().
    """
    validator:       Wallet
    treasury_address:str
    pool:            OptimisticPool

    def _correct_output(self, proof: StakedProof) -> str:
        """
        Testnet mock: deterministic correct output.
        Mainnet: actually re-execute the job or run zkSNARK verify().
        """
        return sha256(f"verified:{proof.input_hash}:{proof.task_type}")

    def process_pending(self, current_block: int,
                        balances: dict[str, int],
                        ledger: "Ledger") -> dict:
        """
        Step 1: Verify every PENDING proof.
                Approve if correct. Slash immediately if wrong.
        Step 2: Settle all APPROVED proofs past their window.
        """
        stats = {"verified": 0, "approved": 0, "slashed": 0, "settled": 0}

        for pid, proof in list(self.pool.pending.items()):
            if proof.status != ProofStatus.PENDING:
                continue
            stats["verified"] += 1
            correct = self._correct_output(proof)

            if proof.output_hash == correct:
                self.pool.approve_proof(pid)
                ledger.record_proof_approved(
                    pid, proof.worker_address, self.validator.address, current_block
                )
                stats["approved"] += 1
            else:
                v_stake = min(proof.stake, balances.get(self.validator.address, 0) // 2)
                ok, msg = self.pool.slash_proof(pid, self.validator.address, v_stake, balances)
                if ok:
                    ledger.record_proof_slashed(
                        pid, proof.worker_address, self.validator.address,
                        proof.stake, current_block
                    )
                    stats["slashed"] += 1

        ledger_records = []
        settled = self.pool.process_block(current_block, balances, ledger_records)
        for rec in ledger_records:
            proof_meta = self.pool.settled  # already removed from pending
            ledger.record_proof_settled(
                rec["proof_id"], rec["worker"], rec["consumer"],
                rec["amount"], 0, current_block
            )
        stats["settled"] = len(settled)
        return stats


class TestBase58Check(unittest.TestCase):
    def test_encode_decode_roundtrip(self):
        data = bytes.fromhex("c4921f6be6a2294e41e0e8336c1c52f6227b17a312f8514880e40d8deab5ca76")
        self.assertEqual(b58decode(b58encode(data)), data)
    def test_address_valid(self):
        self.assertTrue(address_verify(Wallet().address))
    def test_address_tampered(self):
        w = Wallet()
        t = w.address[:-1] + ("X" if w.address[-1] != "X" else "Y")
        self.assertFalse(address_verify(t))
    def test_address_prefix(self):
        self.assertTrue(Wallet().address.startswith("3"))


class TestEd25519(unittest.TestCase):
    def test_sign_verify(self):
        w = Wallet()
        self.assertTrue(w.verify(b"NEWFLOW", w.sign(b"NEWFLOW")))
    def test_wrong_message(self):
        w = Wallet()
        self.assertFalse(w.verify(b"wrong", w.sign(b"correct")))
    def test_wrong_key(self):
        w1, w2 = Wallet(), Wallet()
        self.assertFalse(w2.verify(b"msg", w1.sign(b"msg")))
    def test_restore_from_private_key(self):
        w1 = Wallet()
        self.assertEqual(w1.address, Wallet(w1.private_key_hex).address)


class TestWorkerProfile(unittest.TestCase):
    def setUp(self):
        self.worker = Wallet()
        self.profile = WorkerProfile(
            worker_address=self.worker.address, worker_pubkey=self.worker.public_key.hex(),
            hardware_tier=HardwareTier.MEDIUM, gpu_model="RTX 4090", ram_gb=32,
            supported_tasks=["llm_inference_mid_1B_tokens", "render_frame_4k"],
            registered_at=int(time.time()),
        )
        self.profile.sign(self.worker)
    def test_profile_valid(self):
        self.assertTrue(self.profile.verify())
    def test_can_handle(self):
        self.assertTrue(self.profile.can_handle("render_frame_4k"))
    def test_cannot_handle_unsupported(self):
        self.assertFalse(self.profile.can_handle("llm_pretrain_1M_tokens"))
    def test_tampered_rejected(self):
        self.profile.gpu_model = "RTX 5090"
        self.assertFalse(self.profile.verify())
    def test_register_in_chain(self):
        state = ChainState()
        state.apply_genesis({}, faucet_address="")
        state.register_worker(self.profile)
        self.assertEqual(len(state.get_eligible_workers("render_frame_4k")), 1)
    def test_tier_too_low_rejected(self):
        edge = Wallet()
        bad = WorkerProfile(worker_address=edge.address, worker_pubkey=edge.public_key.hex(),
                            hardware_tier=HardwareTier.EDGE, gpu_model="CPU", ram_gb=4,
                            supported_tasks=["llm_inference_large_1B_tokens"],
                            registered_at=int(time.time()))
        bad.sign(edge)
        state = ChainState()
        state.apply_genesis({}, faucet_address="")
        with self.assertRaises(ValidationError):
            state.register_worker(bad)


class TestProofCarriesPayment(unittest.TestCase):
    def setUp(self):
        self.consumer = Wallet(); self.worker = Wallet()
        ih = sha256("input"); tt = "llm_inference_mid_1B_tokens"
        jid = sha256(tt + ih + self.consumer.address + "nonce")
        self.commitment = JobCommitment(
            job_id=jid, task_type=tt, input_hash=ih,
            compute_units=TASK_CU[tt], fee=50,
            consumer_address=self.consumer.address,
            consumer_pubkey=self.consumer.public_key.hex(),
            worker_address=self.worker.address, timestamp=int(time.time()),
        )
        self.commitment.sign(self.consumer)
        self.pwp = ProofWithPayment(
            job_id=jid, input_hash=ih, output_hash=sha256("output"),
            proof=generate_mock_proof(ih, tt, self.worker.address),
            compute_units=TASK_CU[tt], fee=50,
            worker_address=self.worker.address,
            worker_pubkey=self.worker.public_key.hex(),
            job_commitment=self.commitment,
        )
        self.pwp.sign(self.worker)
    def test_valid(self):
        ok, r = self.pwp.verify_all(); self.assertTrue(ok, r)
    def test_tampered_fee(self):
        self.pwp.fee = 999; self.assertFalse(self.pwp.verify_all()[0])
    def test_wrong_worker(self):
        evil = Wallet()
        self.pwp.worker_address = evil.address; self.pwp.worker_pubkey = evil.public_key.hex()
        self.assertFalse(self.pwp.verify_all()[0])
    def test_invalid_proof(self):
        self.pwp.proof = sha256("fake"); self.assertFalse(self.pwp.verify_all()[0])


class TestBlockSettlement(unittest.TestCase):
    def setUp(self):
        self.consumer = Wallet(); self.worker = Wallet(); self.validator = Wallet()
        ih = sha256("job"); tt = "render_frame_4k"
        jid = sha256(tt + ih + self.consumer.address + "n1")
        c = JobCommitment(job_id=jid, task_type=tt, input_hash=ih,
                          compute_units=TASK_CU[tt], fee=200,
                          consumer_address=self.consumer.address,
                          consumer_pubkey=self.consumer.public_key.hex(),
                          worker_address=self.worker.address, timestamp=int(time.time()))
        c.sign(self.consumer)
        self.pwp = ProofWithPayment(
            job_id=jid, input_hash=ih, output_hash=sha256("frame"),
            proof=generate_mock_proof(ih, tt, self.worker.address),
            compute_units=TASK_CU[tt], fee=200,
            worker_address=self.worker.address, worker_pubkey=self.worker.public_key.hex(),
            job_commitment=c)
        self.pwp.sign(self.worker)
        self.genesis = sha256("genesis")
    def test_block_built(self):
        b = build_block(1, self.genesis, [self.pwp], self.validator)
        self.assertIsNotNone(b); self.assertEqual(b.total_settled, 200)
    def test_hash_deterministic(self):
        b = build_block(1, self.genesis, [self.pwp], self.validator)
        self.assertEqual(b.hash, compute_block_hash(b))
    def test_empty_block_rejected(self):
        self.pwp.proof = "invalid"
        self.assertIsNone(build_block(1, self.genesis, [self.pwp], self.validator))


class TestChainState(unittest.TestCase):
    def setUp(self):
        self.consumer = Wallet(); self.worker = Wallet(); self.validator = Wallet()
        self.state = ChainState()
        self.state.apply_genesis({self.consumer.address: 10_000, self.worker.address: 1_000,
                                   "NETWORK_RESERVE": 855_000}, faucet_address=self.consumer.address)
    def _proof(self, fee=200, cu=50, task="render_frame_4k"):
        ih = sha256("input" + str(time.time())); jid = sha256(task + ih + self.consumer.address + str(time.time()))
        c = JobCommitment(job_id=jid, task_type=task, input_hash=ih, compute_units=cu, fee=fee,
                          consumer_address=self.consumer.address,
                          consumer_pubkey=self.consumer.public_key.hex(),
                          worker_address=self.worker.address, timestamp=int(time.time()))
        c.sign(self.consumer)
        p = ProofWithPayment(job_id=jid, input_hash=ih, output_hash=sha256("out"),
                             proof=generate_mock_proof(ih, task, self.worker.address),
                             compute_units=cu, fee=fee, worker_address=self.worker.address,
                             worker_pubkey=self.worker.public_key.hex(), job_commitment=c)
        p.sign(self.worker); return p
    def test_genesis_balances(self):
        self.assertEqual(self.state.get_balance(self.consumer.address), 10_000)
    def test_apply_block(self):
        b = build_block(1, self.state.tip_hash, [self._proof()], self.validator)
        self.state.apply_block(b)
        self.assertEqual(self.state.get_balance(self.consumer.address), 9_800)
    def test_double_spend(self):
        p = self._proof()
        b1 = build_block(1, self.state.tip_hash, [p], self.validator)
        self.state.apply_block(b1)
        b2 = build_block(2, self.state.tip_hash, [p], self.validator)
        with self.assertRaises(ValidationError): self.state.apply_block(b2)
    def test_rollback(self):
        b1 = build_block(1, self.state.tip_hash, [self._proof(fee=100)], self.validator)
        self.state.apply_block(b1)
        b2 = build_block(2, self.state.tip_hash, [self._proof(fee=100)], self.validator)
        self.state.apply_block(b2)
        self.state.rollback(0)
        self.assertEqual(self.state.get_balance(self.consumer.address), 10_000)
    def test_chain_grows(self):
        for i in range(1, 4):
            b = build_block(i, self.state.tip_hash, [self._proof(fee=100)], self.validator)
            self.state.apply_block(b)
        self.assertEqual(self.state.height, 3)


class TestTransfer(unittest.TestCase):
    def setUp(self):
        self.alice = Wallet(); self.bob = Wallet(); self.carol = Wallet()
        self.faucet = Wallet(); self.validator = Wallet()
        self.state = ChainState()
        self.state.apply_genesis({self.alice.address: 1_000, self.bob.address: 500,
                                   self.faucet.address: FAUCET_TOTAL},
                                  faucet_address=self.faucet.address)
    def _tx(self, sender, receiver, amount, fee=0):
        tx = Transfer(sender=sender.address, sender_pubkey=sender.public_key.hex(),
                      receiver=receiver.address, amount=amount, fee=fee,
                      nonce=self.state.get_nonce(sender.address), timestamp=int(time.time()))
        tx.sign(sender); return tx
    def _claim(self, w):
        c = FaucetClaim(receiver=w.address, receiver_pubkey=w.public_key.hex(), timestamp=int(time.time()))
        c.sign(w); return c
    def test_transfer(self):
        self.state.apply_transfer(self._tx(self.alice, self.bob, 300), self.validator.address)
        self.assertEqual(self.state.get_balance(self.alice.address), 700)
    def test_replay_rejected(self):
        tx = self._tx(self.alice, self.bob, 100)
        self.state.apply_transfer(tx, self.validator.address)
        with self.assertRaises(ValidationError): self.state.apply_transfer(tx, self.validator.address)
    def test_faucet(self):
        self.state.apply_faucet_claim(self._claim(self.carol))
        self.assertEqual(self.state.get_balance(self.carol.address), FAUCET_AMOUNT)
    def test_faucet_double_rejected(self):
        self.state.apply_faucet_claim(self._claim(self.carol))
        with self.assertRaises(ValidationError): self.state.apply_faucet_claim(self._claim(self.carol))


class TestLedger(unittest.TestCase):
    def setUp(self): self.ledger = Ledger()
    def _e(self, t, f, to, amt, blk=1, ref="x"):
        return LedgerEntry(entry_type=t, from_addr=f, to_addr=to, amount=amt,
                           block=blk, timestamp=int(time.time()), ref_id=ref)
    def test_record_and_query(self):
        self.ledger.record(self._e(EntryType.TRANSFER, "alice", "bob", 100))
        self.assertEqual(len(self.ledger.history("alice")), 1)
    def test_proof_lifecycle(self):
        pid = sha256("p1")
        self.ledger.record_proof_pending(pid, "worker", "consumer", 300, 100, "task", 1)
        self.assertEqual(self.ledger.proof_status(pid), "pending")
        self.assertEqual(len(self.ledger.pending_proofs()), 1)
        self.ledger.record_proof_approved(pid, "worker", "validator", 2)
        self.assertEqual(self.ledger.proof_status(pid), "approved")
        self.assertEqual(len(self.ledger.pending_proofs()), 0)
        self.ledger.record_proof_settled(pid, "worker", "consumer", 100, 300, 25)
        self.assertEqual(self.ledger.proof_status(pid), "settled")
    def test_slashed_visible(self):
        pid = sha256("fake")
        self.ledger.record_proof_pending(pid, "attacker", "consumer", 300, 100, "task", 1)
        self.ledger.record_proof_slashed(pid, "attacker", "validator", 300, 2)
        self.assertEqual(self.ledger.proof_status(pid), "slashed")
        self.assertEqual(self.ledger.by_type(EntryType.PROOF_SLASHED)[0].metadata["worker"], "attacker")
    def test_rollback(self):
        self.ledger.record(self._e(EntryType.TRANSFER, "a", "b", 100, blk=5))
        self.ledger.rollback_to_block(3)
        self.assertEqual(self.ledger.total_entries(), 0)


_W  = "3worker_mock"
_C  = "3consumer_mock"
_CH = "3challenger_mock"
_TR = "3treasury_mock"

def _make_staked_proof(job_id="job1", output="correct", block=1, fee=100, stake=300):
    task = "llm_inference_mid_1B_tokens"
    ih   = sha256("input")
    oh   = sha256(f"verified:{ih}:{task}") if output == "correct" else sha256(f"FAKE:{output}")
    return StakedProof(
        job_id=job_id, task_type=task, input_hash=ih, output_hash=oh,
        compute_units=100, fee=fee, stake=stake,
        worker_address=_W, worker_pubkey="pubkey",
        consumer_address=_C, submitted_block=block,
    )

class TestIntegration(unittest.TestCase):
    """End-to-end: Wallet -> Faucet -> Submit proof -> Validator -> Ledger lifecycle."""

    def test_full_honest_cycle(self):
        worker    = Wallet()
        consumer  = Wallet()
        validator = Wallet()
        ledger    = Ledger()
        pool      = OptimisticPool()
        vrf       = ValidatorVerifier(validator, "treasury", pool)

        balances = {
            consumer.address:  1_000,
            worker.address:    500,
            validator.address: 1_000,
        }

        # Worker submits correct proof with stake
        task       = "render_frame_4k"
        ih         = sha256("render input data")
        correct_oh = sha256(f"verified:{ih}:{task}")
        proof_id   = sha256(f"{worker.address}:job1")

        proof = StakedProof(
            job_id=proof_id, task_type=task,
            input_hash=ih, output_hash=correct_oh,
            compute_units=TASK_CU[task], fee=100, stake=300,
            worker_address=worker.address, worker_pubkey=worker.public_key.hex(),
            consumer_address=consumer.address, submitted_block=1,
        )
        ok, msg = pool.submit_proof(proof, balances)
        self.assertTrue(ok, msg)
        ledger.record_proof_pending(proof_id, worker.address, consumer.address,
                                    300, 100, task, block=1)

        # Ledger shows pending
        self.assertEqual(ledger.proof_status(proof_id), "pending")
        self.assertEqual(len(ledger.pending_proofs()), 1)

        # Validator verifies — proof is correct, gets approved
        stats = vrf.process_pending(1 + CHALLENGE_WINDOW_BLOCKS, balances, ledger)
        self.assertEqual(stats["approved"], 1)
        self.assertEqual(stats["slashed"], 0)
        self.assertEqual(stats["settled"], 1)

        # Ledger shows settled
        self.assertEqual(ledger.proof_status(proof_id), "settled")
        self.assertEqual(len(ledger.pending_proofs()), 0)

        # Worker earned fee
        self.assertEqual(balances[worker.address], 500 + 100)   # +fee
        self.assertEqual(balances[consumer.address], 1_000 - 100)  # -fee

    def test_full_attack_cycle(self):
        attacker  = Wallet()
        consumer  = Wallet()
        validator = Wallet()
        ledger    = Ledger()
        pool      = OptimisticPool()
        vrf       = ValidatorVerifier(validator, "treasury", pool)

        balances = {
            consumer.address:  1_000,
            attacker.address:  1_000,
            validator.address: 1_000,
        }
        init_attacker = balances[attacker.address]
        init_consumer = balances[consumer.address]

        # Attacker submits FAKE proof
        task     = "render_frame_4k"
        ih       = sha256("render input data")
        fake_oh  = sha256("FAKE OUTPUT — attacker did not run the job")
        proof_id = sha256(f"{attacker.address}:job_fake")

        proof = StakedProof(
            job_id=proof_id, task_type=task,
            input_hash=ih, output_hash=fake_oh,
            compute_units=TASK_CU[task], fee=100, stake=300,
            worker_address=attacker.address, worker_pubkey=attacker.public_key.hex(),
            consumer_address=consumer.address, submitted_block=1,
        )
        ok, _ = pool.submit_proof(proof, balances)
        self.assertTrue(ok)
        ledger.record_proof_pending(proof_id, attacker.address, consumer.address,
                                    300, 100, task, block=1)

        # Ledger shows pending
        self.assertEqual(ledger.proof_status(proof_id), "pending")

        # Validator catches the fake
        stats = vrf.process_pending(1 + CHALLENGE_WINDOW_BLOCKS, balances, ledger)
        self.assertEqual(stats["slashed"], 1)
        self.assertEqual(stats["settled"], 0)

        # Ledger shows slashed — publicly visible
        self.assertEqual(ledger.proof_status(proof_id), "slashed")
        slashed = ledger.by_type(EntryType.PROOF_SLASHED)
        self.assertEqual(len(slashed), 1)
        self.assertEqual(slashed[0].metadata["worker"], attacker.address)

        # Attacker net negative
        self.assertLess(balances[attacker.address], init_attacker)
        # Consumer unchanged (fee never deducted)
        self.assertEqual(balances[consumer.address], init_consumer)

        net = balances[attacker.address] - init_attacker
        self.assertLess(net, 0)

class TestOptimisticPool(unittest.TestCase):

    def setUp(self):
        self.validator = Wallet()
        self.pool = OptimisticPool()
        self.bal  = {_W: 500, _C: 500, _CH: 500, self.validator.address: 1000, _TR: 0}
        self.vrf  = ValidatorVerifier(self.validator, _TR, self.pool)

    def test_submit_proof_locks_stake(self):
        ok, _ = self.pool.submit_proof(_make_staked_proof(stake=300), self.bal)
        self.assertTrue(ok)
        self.assertEqual(self.bal[_W], 200)  # 500 - 300

    def test_stake_too_low_rejected(self):
        # fee=100 needs stake >= 300 (3x)
        ok, msg = self.pool.submit_proof(_make_staked_proof(fee=100, stake=200), self.bal)
        self.assertFalse(ok)
        self.assertIn("300", msg)

    def test_insufficient_balance_rejected(self):
        ok, _ = self.pool.submit_proof(_make_staked_proof(stake=999), self.bal)
        self.assertFalse(ok)

    def test_pending_never_settles_without_approval(self):
        """Core V2 guarantee: PENDING proof cannot settle — must be APPROVED first."""
        self.pool.submit_proof(_make_staked_proof(output="correct", stake=300), self.bal)
        settled = self.pool.process_block(1 + CHALLENGE_WINDOW_BLOCKS, self.bal, [])
        self.assertEqual(len(settled), 0)
        self.assertEqual(len(self.pool.settled), 0)

    def test_honest_worker_full_cycle(self):
        """Honest worker: validator approves -> settles -> earns fee + stake back."""
        init_w, init_c = self.bal[_W], self.bal[_C]
        proof = _make_staked_proof(output="correct", block=1, fee=100, stake=300)
        self.pool.submit_proof(proof, self.bal)
        stats = self.vrf.process_pending(1 + CHALLENGE_WINDOW_BLOCKS, self.bal, Ledger())
        self.assertEqual(stats["approved"], 1)
        self.assertEqual(stats["settled"],  1)
        self.assertEqual(stats["slashed"],  0)
        # Worker: 500-300(stake lock)+100(fee)+300(stake back) = 600
        self.assertEqual(self.bal[_W], init_w + 100)  # net +100
        self.assertEqual(self.bal[_C], init_c - 100)  # paid fee

    def test_fake_proof_slashed_by_validator(self):
        """Attack 2 fix: validator auto-slashes fake proof before it can settle."""
        init_w = self.bal[_W]
        fake = _make_staked_proof(output="FAKE", fee=100, stake=300)
        self.pool.submit_proof(fake, self.bal)
        stats = self.vrf.process_pending(1 + CHALLENGE_WINDOW_BLOCKS, self.bal, Ledger())
        self.assertEqual(stats["slashed"], 1)
        self.assertEqual(stats["settled"], 0)
        self.assertLess(self.bal.get(_W, 0), init_w)  # attacker lost ECU

    def test_challenge_same_output_rejected(self):
        proof = _make_staked_proof(output="correct", stake=300)
        self.pool.submit_proof(proof, self.bal)
        ch = Challenge(proof_id=proof.proof_id(), challenger_address=_CH,
                       challenger_pubkey="pub",
                       correct_output_hash=proof.output_hash,  # same as worker
                       challenge_stake=100, challenged_block=5)
        ok, msg = self.pool.submit_challenge(ch, self.bal)
        self.assertFalse(ok)
        self.assertIn("no dispute", msg)

    def test_challenge_after_window_rejected(self):
        proof = _make_staked_proof(block=1, stake=300)
        self.pool.submit_proof(proof, self.bal)
        ch = Challenge(proof_id=proof.proof_id(), challenger_address=_CH,
                       challenger_pubkey="pub", correct_output_hash=sha256("diff"),
                       challenge_stake=100,
                       challenged_block=1 + CHALLENGE_WINDOW_BLOCKS + 1)
        ok, msg = self.pool.submit_challenge(ch, self.bal)
        self.assertFalse(ok)
        self.assertIn("window", msg)

    def test_challenge_won_slashes_worker(self):
        """Human challenger catches fake proof — gets 100% of worker stake."""
        proof = _make_staked_proof(output="WRONG", fee=100, stake=300)
        self.pool.submit_proof(proof, self.bal)
        # CH: 500 - 100(stake) = 400
        ch = Challenge(proof_id=proof.proof_id(), challenger_address=_CH,
                       challenger_pubkey="pub",
                       correct_output_hash=sha256("correct"),
                       challenge_stake=100, challenged_block=5)
        self.pool.submit_challenge(ch, self.bal)
        ok, msg = self.pool.resolve_challenge(
            ch.challenge_id(), sha256("correct"), self.bal, _TR
        )
        self.assertTrue(ok)
        # CH: 400 + 100(back) + 300(worker slash) = 800
        self.assertEqual(self.bal[_CH], 800)

    def test_challenge_lost_slashes_challenger(self):
        proof = _make_staked_proof(output="correct", fee=100, stake=300)
        self.pool.submit_proof(proof, self.bal)
        ch = Challenge(proof_id=proof.proof_id(), challenger_address=_CH,
                       challenger_pubkey="pub",
                       correct_output_hash=sha256("wrong_claim"),
                       challenge_stake=100, challenged_block=5)
        self.pool.submit_challenge(ch, self.bal)
        ok, _ = self.pool.resolve_challenge(
            ch.challenge_id(), sha256("correct"), self.bal, _TR
        )
        self.assertTrue(ok)
        # W: 200 + 100(challenger stake) = 300
        self.assertEqual(self.bal[_W], 300)
        # CH: 400 (stake lost)
        self.assertEqual(self.bal[_CH], 400)

    def test_stats(self):
        self.pool.submit_proof(_make_staked_proof(stake=300), self.bal)
        s = self.pool.stats()
        self.assertEqual(s["pending"], 1)
        self.assertEqual(s["total_staked_ecu"], 300)


if __name__ == "__main__":
    import sys

    if "--demo" in sys.argv:
        print("=" * 60)
        print("NEWFLOW v0.3 — Distributed AI Compute Demo")
        print("=" * 60)

        # Three workers with different hardware — all contributing to AI
        edge_worker   = Wallet()
        medium_worker = Wallet()
        heavy_worker  = Wallet()
        consumer      = Wallet()
        validator     = Wallet()

        state = ChainState()
        state.apply_genesis({consumer.address: 5_000}, faucet_address="")

        # Register workers — no permission required
        for w, tier, gpu, tasks in [
            (edge_worker,   HardwareTier.EDGE,   "CPU-only",  ["embedding_1M_tokens", "tokenize_1M_tokens"]),
            (medium_worker, HardwareTier.MEDIUM, "RTX 4090",  ["llm_inference_mid_1B_tokens", "render_frame_4k"]),
            (heavy_worker,  HardwareTier.HEAVY,  "A100 x2",   ["llm_inference_large_1B_tokens", "image_generation_4k"]),
        ]:
            profile = WorkerProfile(
                worker_address=w.address, worker_pubkey=w.public_key.hex(),
                hardware_tier=tier, gpu_model=gpu, ram_gb=16,
                supported_tasks=tasks, registered_at=int(time.time()),
            )
            profile.sign(w)
            state.register_worker(profile)
            print(f"  Worker registered: {gpu} ({tier.value}) -> {w.address[:20]}...")

        # Find eligible workers for an AI job
        task = "llm_inference_mid_1B_tokens"
        eligible = state.get_eligible_workers(task)
        print(f"\nJob: {task}")
        print(f"Eligible workers: {len(eligible)}")

        print("\nVerify proof = settle payment. No permission. No judge.")
    else:
        print("=" * 60)
        print("NEWFLOW v0.3 — Unit Tests")
        print("=" * 60)
        loader = unittest.TestLoader()
        suite  = unittest.TestSuite()
        for cls in [
            TestBase58Check,
            TestEd25519,
            TestWorkerProfile,
            TestProofCarriesPayment,
            TestBlockSettlement,
            TestChainState,
            TestTransfer,
            TestLedger,
            TestOptimisticPool,
        ]:
            suite.addTests(loader.loadTestsFromTestCase(cls))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        print(f"\n{'OK' if result.wasSuccessful() else 'FAILED'} — {result.testsRun} tests")


