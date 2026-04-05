LINUX_TARGET = x86_64-unknown-linux-musl
LINUX_ARM_TARGET = aarch64-unknown-linux-musl

# Makefile — Standard build interface for all agents
#
# This is the canonical build/test/lint entry point for the ostk project.
# Any agent (human or AI) should use these targets for consistent builds.
# Run `make` or `make all` for the full pipeline.

.PHONY: all build check clippy test e2e fmt fmt-check clean install \
       score score-all score-status score-collate \
       bench-binary bench-binary-arm bench-setup

all: fmt-check clippy build test e2e

build:
	cargo build

check:
	cargo check

clippy:
	cargo clippy -- -D warnings

test:
	cargo test

e2e: build
	bash tests/e2e.sh

fmt:
	cargo fmt

fmt-check:
	cargo fmt -- --check

clean:
	cargo clean

install:
	cargo install --path . --bin ostk --features embeddings
	@# macOS marks cargo-built binaries with com.apple.provenance,
	@# causing ASP to kill subprocess invocations. Re-sign to fix.
	@codesign --force --sign - "$$(which ostk)" 2>/dev/null || true
	@# Cross-compile Linux binary for `ostk bench --local`
	@cargo build --release --target $(LINUX_TARGET) 2>/dev/null && \
		echo "bench binary: target/$(LINUX_TARGET)/release/ostk" || \
		echo "note: Linux cross-compile skipped (run \`rustup target add $(LINUX_TARGET)\` to enable)"

smoke: install
	@rm -f /tmp/ostk-smoke-test.txt
	@ostk run /tmp/af-smoke.af 2>&1 | tail -5
	@test -f /tmp/ostk-smoke-test.txt && echo "PASS: ostk run works" || { echo "FAIL: tool execution broken"; exit 1; }

# Full pre-release pipeline: build, test, install, verify binary works
release: all install smoke

# --- Bench dispatch ---

# Run all benchmarks for one model:  make score MODEL=claude-haiku
# Run with more parallelism:         make score MODEL=claude-haiku PARALLEL=8
score:
	@test -n "$(MODEL)" || { echo "usage: make score MODEL=<model-id>"; exit 1; }
	bash bench/dispatch.sh --model $(MODEL) --parallel $(or $(PARALLEL),4)

# Run all models x all benchmarks
score-all:
	bash bench/dispatch.sh --parallel $(or $(PARALLEL),4)

# See what would run without running it
score-status:
	bash bench/dispatch.sh --dry-run

# Re-collate results into leaderboard/scores.json
score-collate:
	bash bench/collate.sh

# --- Cross-compile for bench ---

# Cross-compile ostk for Linux x86_64 (used by `ostk bench --local`)
bench-binary:
	@echo "cross-compiling ostk for $(LINUX_TARGET)..."
	cargo build --release --target $(LINUX_TARGET)
	@echo "binary: target/$(LINUX_TARGET)/release/ostk"
	@ls -lh target/$(LINUX_TARGET)/release/ostk

# Cross-compile ostk for Linux ARM64
bench-binary-arm:
	@echo "cross-compiling ostk for $(LINUX_ARM_TARGET)..."
	cargo build --release --target $(LINUX_ARM_TARGET)
	@echo "binary: target/$(LINUX_ARM_TARGET)/release/ostk"
	@ls -lh target/$(LINUX_ARM_TARGET)/release/ostk

# Build + install + cross-compile: full bench dev setup
bench-setup: install bench-binary
	@echo "ready: ostk installed + Linux binary built for bench --local"
