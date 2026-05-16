#!/bin/bash

# 

# HEXIS Block 0 Genesis Minting Script

# =====================================

# Hexis Foundation - Singapore

# contact@hexisfoundation.org

# 

# WARNING: This operation is IRREVERSIBLE.

# Block 0 can be minted only ONCE in the protocol’s lifetime.

# 

# Prerequisites before running:

# 1. SSH into VPS as root

# 2. HEXIS API service stopped

# 3. Database backed up

# 4. Bailout event headline confirmed and verified

# 5. Source URL accessible and archived (web.archive.org)

# 

# Usage:

# sudo bash mint_block_0.sh

# 

# What this script does:

# 1. Validates database has no prior Block 0

# 2. Collects bailout event metadata

# 3. Shows preview for human review

# 4. Requires explicit “MINT BLOCK ZERO” confirmation

# 5. Executes Genesis Event + Genesis Burn atomically

# 6. Outputs cryptographic hash for verification

# 7. Locks further genesis attempts permanently

# 

set -euo pipefail

# ============================================================

# CONFIGURATION

# ============================================================

DB_PATH=”/opt/hexis_newflow/hexis.db”
BACKUP_DIR=”/opt/hexis_newflow/backups”
LOG_FILE=”/opt/hexis_newflow/genesis.log”

# Genesis Burn allocation (per whitepaper Section 6)

# 768,000 HEXIS = 6.0% of 12,800,000 total supply

GENESIS_BURN_TOTAL=768000
GENESIS_BURN_SLOTS=5
GENESIS_BURN_PER_SLOT=153600

# ============================================================

# COLORS (for human-readable output)

# ============================================================

RED=’\033[0;31m’
GREEN=’\033[0;32m’
YELLOW=’\033[1;33m’
BLUE=’\033[0;34m’
BOLD=’\033[1m’
NC=’\033[0m’

# ============================================================

# PRE-FLIGHT CHECKS

# ============================================================

echo “”
echo -e “${BOLD}HEXIS Block 0 Genesis Minting${NC}”
echo -e “${BOLD}==============================${NC}”
echo “”

# Must run as root

if [ “$EUID” -ne 0 ]; then
echo -e “${RED}ERROR: Must run as root (sudo bash mint_block_0.sh)${NC}”
exit 1
fi

# Database must exist

if [ ! -f “$DB_PATH” ]; then
echo -e “${RED}ERROR: Database not found at $DB_PATH${NC}”
exit 1
fi

# Check API is stopped (port 8401 should not be in use)

if ss -tlnp 2>/dev/null | grep -q “:8401 “; then
echo -e “${RED}ERROR: HEXIS API still running on port 8401${NC}”
echo -e “${YELLOW}Stop it first: sudo systemctl stop hexis-api${NC}”
echo -e “${YELLOW}Or: sudo pkill -f hexis_api${NC}”
exit 1
fi

# Check if Block 0 already minted

GENESIS_EXISTS=$(sqlite3 “$DB_PATH” “SELECT count(*) FROM sqlite_master WHERE type=‘table’ AND name=‘genesis’;” 2>/dev/null || echo “0”)
if [ “$GENESIS_EXISTS” == “1” ]; then
SEALED=$(sqlite3 “$DB_PATH” “SELECT count(*) FROM genesis WHERE sealed=1;” 2>/dev/null || echo “0”)
if [ “$SEALED” -gt “0” ]; then
echo -e “${RED}ERROR: Block 0 has already been minted.${NC}”
echo -e “${RED}This operation cannot be repeated.${NC}”
sqlite3 “$DB_PATH” “SELECT ’Sealed at: ’ || datetime(sealed_at, ‘unixepoch’) FROM genesis WHERE sealed=1 LIMIT 1;”
exit 1
fi
fi

# Create backup directory

mkdir -p “$BACKUP_DIR”

echo -e “${GREEN}Pre-flight checks passed.${NC}”
echo “”

# ============================================================

# INPUT COLLECTION

# ============================================================

echo -e “${BOLD}STEP 1: Bailout Event Metadata${NC}”
echo -e “${YELLOW}Provide the news event that justifies HEXIS existence.${NC}”
echo “”

read -p “Event date (YYYY-MM-DD): “ EVENT_DATE
if ! echo “$EVENT_DATE” | grep -Eq ‘^[0-9]{4}-[0-9]{2}-[0-9]{2}$’; then
echo -e “${RED}ERROR: Date must be YYYY-MM-DD format${NC}”
exit 1
fi

read -p “Headline (verbatim from news source): “ HEADLINE
if [ -z “$HEADLINE” ] || [ ${#HEADLINE} -lt 20 ]; then
echo -e “${RED}ERROR: Headline must be at least 20 characters${NC}”
exit 1
fi

read -p “Source publication (e.g. Reuters, FT, Bloomberg): “ SOURCE_PUB
if [ -z “$SOURCE_PUB” ]; then
echo -e “${RED}ERROR: Source required${NC}”
exit 1
fi

read -p “Source URL: “ SOURCE_URL
if ! echo “$SOURCE_URL” | grep -Eq ‘^https?://’; then
echo -e “${RED}ERROR: URL must start with http:// or https://${NC}”
exit 1
fi

read -p “Archive.org URL (web.archive.org snapshot): “ ARCHIVE_URL
if [ -z “$ARCHIVE_URL” ]; then
echo -e “${YELLOW}WARNING: No archive URL. Strongly recommended for permanence.${NC}”
read -p “Continue without archive? (no/yes): “ ARCHIVE_CONFIRM
if [ “$ARCHIVE_CONFIRM” != “yes” ]; then
echo “Aborted. Get web.archive.org snapshot first.”
exit 0
fi
ARCHIVE_URL=”(none)”
fi

read -p “Brief context (1-2 sentences explaining significance): “ CONTEXT
if [ -z “$CONTEXT” ]; then
echo -e “${RED}ERROR: Context required${NC}”
exit 1
fi

# ============================================================

# COMPOSE GENESIS MESSAGE

# ============================================================

GENESIS_TIMESTAMP=$(date +%s)
GENESIS_DATETIME=$(date -u +”%Y-%m-%dT%H:%M:%SZ”)

GENESIS_MESSAGE=“HEXIS Block 0 - $EVENT_DATE - $SOURCE_PUB: "$HEADLINE" - $CONTEXT - Source: $SOURCE_URL - Archive: $ARCHIVE_URL - Sealed: $GENESIS_DATETIME”

# Compute SHA256 hash for cryptographic anchoring

GENESIS_HASH=$(echo -n “$GENESIS_MESSAGE” | sha256sum | cut -d’ ’ -f1)

# ============================================================

# PREVIEW

# ============================================================

echo “”
echo -e “${BOLD}STEP 2: Preview Genesis Block${NC}”
echo -e “${BOLD}==============================${NC}”
echo “”
echo -e “${BLUE}Event Date:${NC}    $EVENT_DATE”
echo -e “${BLUE}Source:${NC}        $SOURCE_PUB”
echo -e “${BLUE}Headline:${NC}      $HEADLINE”
echo -e “${BLUE}Context:${NC}       $CONTEXT”
echo -e “${BLUE}URL:${NC}           $SOURCE_URL”
echo -e “${BLUE}Archive:${NC}       $ARCHIVE_URL”
echo -e “${BLUE}Sealed at:${NC}     $GENESIS_DATETIME (UTC)”
echo -e “${BLUE}Timestamp:${NC}     $GENESIS_TIMESTAMP”
echo “”
echo -e “${BLUE}Genesis hash (SHA256):${NC}”
echo -e “  ${GREEN}$GENESIS_HASH${NC}”
echo “”
echo -e “${BLUE}Full genesis message:${NC}”
echo “  $GENESIS_MESSAGE”
echo “”
echo -e “${BLUE}Genesis Burn:${NC}”
echo -e “  Total: ${GENESIS_BURN_TOTAL} HEXIS (6.0% of supply)”
echo -e “  Distributed: ${GENESIS_BURN_PER_SLOT} HEXIS x ${GENESIS_BURN_SLOTS} addresses”
echo -e “  Mechanism: addresses with provably no private key”
echo “”

# ============================================================

# FINAL CONFIRMATION

# ============================================================

echo -e “${RED}${BOLD}WARNING: This operation is IRREVERSIBLE.${NC}”
echo -e “${RED}Block 0 can only be minted once in the protocol’s lifetime.${NC}”
echo -e “${RED}Once sealed, the genesis message is permanent.${NC}”
echo “”

read -p “Type ‘MINT BLOCK ZERO’ to proceed (anything else aborts): “ FINAL_CONFIRM

if [ “$FINAL_CONFIRM” != “MINT BLOCK ZERO” ]; then
echo “”
echo -e “${YELLOW}Aborted. No changes made.${NC}”
exit 0
fi

# ============================================================

# BACKUP

# ============================================================

BACKUP_FILE=”$BACKUP_DIR/pre_genesis_$(date +%Y%m%d_%H%M%S).db”
echo “”
echo -e “${BLUE}Backing up database to:${NC} $BACKUP_FILE”
cp “$DB_PATH” “$BACKUP_FILE”
echo -e “${GREEN}Backup complete.${NC}”
sleep 1

# ============================================================

# ATOMIC GENESIS MUTATION

# ============================================================

echo “”
echo -e “${BOLD}STEP 3: Minting Block 0…${NC}”

# Generate 5 burn addresses with provably-no-private-key pattern

# Format: 0x48 (H for HEXIS) || sha256(genesis_hash + slot)[:30] || slot_byte

BURN_ADDRESSES=()
for slot in 0 1 2 3 4; do
SLOT_HASH=$(echo -n “${GENESIS_HASH}*burn_slot*${slot}” | sha256sum | cut -c1-60)
SLOT_BYTE=$(printf ‘%02x’ “$slot”)
ADDR=“0x48${SLOT_HASH}${SLOT_BYTE}”
BURN_ADDRESSES+=(”$ADDR”)
done

# Execute all mutations in single transaction

sqlite3 “$DB_PATH” <<SQL
BEGIN IMMEDIATE TRANSACTION;

– Genesis lock table
CREATE TABLE IF NOT EXISTS genesis (
id INTEGER PRIMARY KEY,
event_date TEXT NOT NULL,
headline TEXT NOT NULL,
source_publication TEXT NOT NULL,
source_url TEXT NOT NULL,
archive_url TEXT,
context TEXT NOT NULL,
full_message TEXT NOT NULL,
genesis_hash TEXT NOT NULL UNIQUE,
sealed_at INTEGER NOT NULL,
sealed INTEGER NOT NULL DEFAULT 1
);

– Insert genesis record
INSERT INTO genesis(event_date, headline, source_publication, source_url, archive_url, context, full_message, genesis_hash, sealed_at, sealed)
VALUES(’$EVENT_DATE’, ‘$HEADLINE’, ‘$SOURCE_PUB’, ‘$SOURCE_URL’, ‘$ARCHIVE_URL’, ‘$CONTEXT’, ‘$GENESIS_MESSAGE’, ‘$GENESIS_HASH’, $GENESIS_TIMESTAMP, 1);

– Insert as Event #1 (genesis event in main events table)
INSERT INTO events(event_id, actor_id, country, fee, event_type, tier, hexis_minted, timestamp)
VALUES(1, ‘GENESIS_BLOCK_0’, ‘GLOBAL’, 0, ‘genesis_message’, 1, 0, $GENESIS_TIMESTAMP);

– Genesis Burn: 5 addresses x 153,600 HEXIS
CREATE TABLE IF NOT EXISTS genesis_burns (
burn_id INTEGER PRIMARY KEY AUTOINCREMENT,
address TEXT NOT NULL UNIQUE,
amount REAL NOT NULL,
slot INTEGER NOT NULL,
sealed_at INTEGER NOT NULL
);

INSERT INTO genesis_burns(address, amount, slot, sealed_at) VALUES
(’${BURN_ADDRESSES[0]}’, $GENESIS_BURN_PER_SLOT, 0, $GENESIS_TIMESTAMP),
(’${BURN_ADDRESSES[1]}’, $GENESIS_BURN_PER_SLOT, 1, $GENESIS_TIMESTAMP),
(’${BURN_ADDRESSES[2]}’, $GENESIS_BURN_PER_SLOT, 2, $GENESIS_TIMESTAMP),
(’${BURN_ADDRESSES[3]}’, $GENESIS_BURN_PER_SLOT, 3, $GENESIS_TIMESTAMP),
(’${BURN_ADDRESSES[4]}’, $GENESIS_BURN_PER_SLOT, 4, $GENESIS_TIMESTAMP);

– Update network stats with burn
INSERT OR REPLACE INTO network_stats(key, value) VALUES(‘genesis_burned_hexis’, $GENESIS_BURN_TOTAL);
INSERT OR REPLACE INTO network_stats(key, value) VALUES(‘genesis_sealed_at’, $GENESIS_TIMESTAMP);

COMMIT;
SQL

if [ $? -ne 0 ]; then
echo -e “${RED}ERROR: Genesis mutation failed. Check database integrity.${NC}”
echo -e “${YELLOW}Restore from backup: cp $BACKUP_FILE $DB_PATH${NC}”
exit 1
fi

# ============================================================

# VERIFICATION

# ============================================================

echo -e “${GREEN}Genesis transaction committed.${NC}”
echo “”
echo -e “${BOLD}STEP 4: Verifying Block 0…${NC}”

VERIFY_HASH=$(sqlite3 “$DB_PATH” “SELECT genesis_hash FROM genesis WHERE sealed=1;”)
VERIFY_BURNS=$(sqlite3 “$DB_PATH” “SELECT count(*) FROM genesis_burns;”)
VERIFY_BURN_TOTAL=$(sqlite3 “$DB_PATH” “SELECT sum(amount) FROM genesis_burns;”)

if [ “$VERIFY_HASH” != “$GENESIS_HASH” ]; then
echo -e “${RED}ERROR: Hash verification failed.${NC}”
exit 1
fi

if [ “$VERIFY_BURNS” != “5” ]; then
echo -e “${RED}ERROR: Burn count incorrect.${NC}”
exit 1
fi

if [ “$VERIFY_BURN_TOTAL” != “$GENESIS_BURN_TOTAL” ]; then
echo -e “${RED}ERROR: Burn total incorrect.${NC}”
exit 1
fi

echo -e “${GREEN}All verifications passed.${NC}”

# ============================================================

# LOG TO PERMANENT FILE

# ============================================================

# cat >> “$LOG_FILE” <<LOG

# HEXIS Block 0 Sealed

Sealed at:        $GENESIS_DATETIME
Timestamp:        $GENESIS_TIMESTAMP
Event date:       $EVENT_DATE
Source:           $SOURCE_PUB
Headline:         $HEADLINE
URL:              $SOURCE_URL
Archive:          $ARCHIVE_URL
Context:          $CONTEXT
Genesis hash:     $GENESIS_HASH

Genesis burn (5 x 153,600 = 768,000 HEXIS):
Slot 0: ${BURN_ADDRESSES[0]}
Slot 1: ${BURN_ADDRESSES[1]}
Slot 2: ${BURN_ADDRESSES[2]}
Slot 3: ${BURN_ADDRESSES[3]}
Slot 4: ${BURN_ADDRESSES[4]}

Backup file:      $BACKUP_FILE

Full message:
$GENESIS_MESSAGE

===============================================
LOG

# ============================================================

# OUTPUT

# ============================================================

echo “”
echo -e “${BOLD}${GREEN}===============================================${NC}”
echo -e “${BOLD}${GREEN}    HEXIS BLOCK 0 SEALED${NC}”
echo -e “${BOLD}${GREEN}===============================================${NC}”
echo “”
echo -e “${BLUE}Genesis hash:${NC}”
echo -e “  ${GREEN}$GENESIS_HASH${NC}”
echo “”
echo -e “${BLUE}Sealed at:${NC} $GENESIS_DATETIME”
echo “”
echo -e “${BLUE}Genesis burn addresses:${NC}”
for i in 0 1 2 3 4; do
echo “  Slot $i: ${BURN_ADDRESSES[$i]}”
done
echo “”
echo -e “${BLUE}Log written to:${NC} $LOG_FILE”
echo -e “${BLUE}Backup at:${NC}      $BACKUP_FILE”
echo “”
echo -e “${YELLOW}NEXT STEPS:${NC}”
echo “  1. Restart HEXIS API:  sudo systemctl start hexis-api”
echo “  2. Verify via API:     curl https://api.hexisfoundation.org/genesis”
echo “  3. Update whitepaper:  push v0.6.x with Block 0 message to GitHub”
echo “  4. Update website:     announce Block 0 sealed”
echo “  5. Post Twitter:       Thread 1 with genesis hash reference”
echo “”
echo -e “${BOLD}The protocol now has a beginning.${NC}”
echo “”
