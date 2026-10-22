from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .core import (
    BuiltInDecider,
    Engine,
    OpenAICompatibleDecider,
    StateStore,
    app_data_dir,
)
from .live import LiveEngine
from .advanced_live import AdvancedSpotEngine
from .wallets.advanced_trade import AdvancedTradeSpot, AdvancedTradeVault
from .bean_growth_adaptive import BeanAdaptiveGrowthGate
from .bean_strategy_lab import BeanStrategyLab
from .wallets.cdp_wallet import CdpWallet, CredentialVault

APP_NAME = os.getenv("AUTOCAPITAL_APP_NAME", "Autonomous Capital Lab")


class StatCard(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("statCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        caption = QLabel(title.upper())
        caption.setObjectName("statCaption")
        self.caption = caption
        self.value = QLabel("--")
        self.value.setObjectName("statValue")
        layout.addWidget(caption)
        layout.addWidget(self.value)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.store = StateStore()
        self.engine = Engine(self.store)
        self.bean_strategy_lab = BeanStrategyLab()
        self.bean_last_event = None
        self.bean_timer = QTimer(self)
        self.bean_timer.timeout.connect(self.refresh_bean_lab)
        self.bean_timer.start(1800)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.run_cycle)

        self.setWindowTitle(APP_NAME)
        self.resize(1180, 790)
        self.setMinimumSize(980, 680)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(14)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        self.subtitle = QLabel("Autonomous capital experiment · synthetic sandbox")
        self.subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        header.addLayout(title_box)
        header.addStretch()

        self.status = QLabel("STOPPED")
        self.status.setObjectName("statusStopped")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setFixedWidth(110)
        header.addWidget(self.status)
        outer.addLayout(header)

        stats = QHBoxLayout()
        self.net_card = StatCard("Net liquidation")
        self.cash_card = StatCard("Cash")
        self.pnl_card = StatCard("Profit / loss")
        self.multiple_card = StatCard("Multiple")
        for card in (self.net_card, self.cash_card, self.pnl_card, self.multiple_card):
            stats.addWidget(card)
        outer.addLayout(stats)

        body = QHBoxLayout()
        body.setSpacing(14)

        controls = QFrame()
        controls.setObjectName("panel")
        controls.setFixedWidth(350)
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(16, 16, 16, 16)

        controls_title = QLabel("AUTONOMY")
        controls_title.setObjectName("sectionTitle")
        controls_layout.addWidget(controls_title)

        form = QFormLayout()
        form.setVerticalSpacing(10)

        self.bankroll = QDoubleSpinBox()
        self.bankroll.setPrefix("$")
        self.bankroll.setDecimals(2)
        self.bankroll.setRange(0.00, 1_000_000)
        self.bankroll.setValue(self.store.starting_cash or 10.0)
        self.bankroll.setEnabled(False)
        form.addRow("Starting value", self.bankroll)

        self.execution = QComboBox()
        self.execution.addItems(
            [
                "Synthetic sandbox",
                "Coinbase live · Base Sepolia",
                "Coinbase live · Base",
                "Coinbase live · Advanced Spot",
            ]
        )
        self.execution.currentIndexChanged.connect(self.execution_changed)
        form.addRow("Execution", self.execution)

        self.interval = QDoubleSpinBox()
        self.interval.setSuffix(" sec")
        self.interval.setDecimals(1)
        self.interval.setRange(0.5, 3600.0)
        self.interval.setValue(3.0)
        form.addRow("Decision cycle", self.interval)

        self.mode = QComboBox()
        self.mode.addItems(["Built-in autonomous", "OpenAI-compatible AI"])
        self.mode.currentIndexChanged.connect(self.toggle_ai_fields)
        form.addRow("Decision engine", self.mode)

        self.endpoint = QLineEdit("http://localhost:11434/v1/chat/completions")
        self.endpoint.setEnabled(False)
        form.addRow("AI endpoint", self.endpoint)

        self.model = QLineEdit("qwen3:8b")
        self.model.setEnabled(False)
        form.addRow("Model", self.model)

        controls_layout.addLayout(form)

        self.coinbase_button = QPushButton("CONFIGURE COINBASE")
        self.coinbase_button.clicked.connect(self.configure_coinbase)
        controls_layout.addWidget(self.coinbase_button)

        api_note = QLabel(
            "Coinbase credentials are stored in Windows Credential Manager. "
            "Base uses the CDP smart account; Advanced Spot uses a "
            "Coinbase Advanced trading API key and its own filled-order ledger."
        )
        api_note.setWordWrap(True)
        api_note.setObjectName("hint")
        controls_layout.addWidget(api_note)

        self.start_button = QPushButton("START AUTONOMY")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self.start_autonomy)
        controls_layout.addWidget(self.start_button)

        self.stop_button = QPushButton("STOP")
        self.stop_button.clicked.connect(self.stop_autonomy)
        self.stop_button.setEnabled(False)
        controls_layout.addWidget(self.stop_button)

        self.reset_button = QPushButton("RESET SYNTHETIC TO $10")
        self.reset_button.setObjectName("dangerButton")
        self.reset_button.clicked.connect(self.reset_experiment)
        controls_layout.addWidget(self.reset_button)

        controls_layout.addStretch()

        self.boundary = QLabel(
            "Account boundary\n"
            "No leverage. No negative cash. No outside funding. "
            "Within the selected bankroll, trades execute without per-trade approval."
        )
        self.boundary.setWordWrap(True)
        self.boundary.setObjectName("boundary")
        controls_layout.addWidget(self.boundary)

        body.addWidget(controls)

        right = QVBoxLayout()
        school = QFrame()
        school.setObjectName("beanPanel")
        school_layout = QVBoxLayout(school)
        school_layout.setContentsMargins(15, 13, 15, 13)
        school_layout.setSpacing(8)
        school_title = QLabel("🌱  BEAN STRATEGY SCHOOL")
        school_title.setObjectName("beanTitle")
        school_layout.addWidget(school_title)
        school_description = QLabel(
            "Brute-force research, retain lessons, automatically select qualified strategies. "
            "Live Advanced Spot uses separate Q-policy feedback for "
            "executable trades. This strategy research panel tracks longer "
            "holdout experiments, not open Coinbase orders."
        )
        school_description.setWordWrap(True)
        school_description.setObjectName("hint")
        school_layout.addWidget(school_description)
        school_actions = QHBoxLayout()
        self.bean_run_button = QPushButton("RUN EXPERIMENT")
        self.bean_run_button.setObjectName("beanButton")
        self.bean_run_button.clicked.connect(self.run_bean_lab)
        school_actions.addWidget(self.bean_run_button)
        self.bean_retry_button = QPushButton("TRY AGAIN")
        self.bean_retry_button.clicked.connect(self.run_bean_lab)
        self.bean_retry_button.setEnabled(False)
        school_actions.addWidget(self.bean_retry_button)
        school_layout.addLayout(school_actions)
        self.bean_result = QLabel("BEAN is ready for an independent research exam.")
        self.bean_result.setWordWrap(True)
        self.bean_result.setObjectName("beanResult")
        school_layout.addWidget(self.bean_result)
        right.addWidget(school)

        positions_title = QLabel("POSITIONS")
        positions_title.setObjectName("sectionTitle")
        right.addWidget(positions_title)

        self.positions = QTableWidget(0, 5)
        self.positions.setHorizontalHeaderLabels(
            ["Asset", "Quantity", "Avg cost", "Price", "Market value"]
        )
        self.positions.horizontalHeader().setStretchLastSection(True)
        self.positions.setEditTriggers(QTableWidget.NoEditTriggers)
        self.positions.setSelectionBehavior(QTableWidget.SelectRows)
        right.addWidget(self.positions, 2)

        decisions_title = QLabel("DECISION JOURNAL")
        decisions_title.setObjectName("sectionTitle")
        right.addWidget(decisions_title)

        self.decisions = QTableWidget(0, 6)
        self.decisions.setHorizontalHeaderLabels(
            ["Time", "Action", "Asset", "Fraction", "Status", "Rationale"]
        )
        self.decisions.horizontalHeader().setStretchLastSection(True)
        self.decisions.setEditTriggers(QTableWidget.NoEditTriggers)
        self.decisions.setSelectionBehavior(QTableWidget.SelectRows)
        right.addWidget(self.decisions, 3)

        body.addLayout(right, 1)
        outer.addLayout(body, 1)

        self.apply_style()
        self.refresh()
        self.refresh_bean_lab()

    def refresh_bean_lab(self) -> None:
        try:
            report = self.bean_strategy_lab.status()
        except Exception as exc:
            self.bean_result.setText(f"BEAN Lab unavailable: {exc}")
            return
        latest = report.get("latest")
        self.bean_last_event = latest
        running = bool(report.get("running"))
        self.bean_run_button.setEnabled(latest is None and not running)
        self.bean_retry_button.setEnabled(latest is not None and not running)
        qualified = bool(((latest or {}).get("report") or {}).get("beats_cash_and_btc"))
        if not latest:
            self.bean_result.setText("Ready · Research only · No trades")
        elif running:
            self.bean_result.setText(
                "BEAN is evolving strategies using public Coinbase candles. "
                "Previous lessons remain saved."
            )
        elif latest.get("error"):
            self.bean_result.setText(
                f"Exam #{latest['ordinal']} failed: {latest['error']}. Try again to retain lessons."
            )
        elif latest.get("report"):
            data = latest["report"]
            outcome = float(data["holdout"]["net_return"]) * 100
            self.bean_result.setText(
                f"Exam #{latest['ordinal']} · {data['unique_candidates']} candidates · "
                f"Holdout {outcome:+.2f}% · BTC {float(data['btc_return'])*100:+.2f}% · "
                f"{'Auto-selected' if qualified else 'Rejected; learning continues'} "
                f"· {len(report['history'])} recent lessons"
            )
        else:
            self.bean_result.setText("The previous experiment was interrupted. Retry to continue learning.")

    def run_bean_lab(self) -> None:
        try:
            self.bean_strategy_lab.start()
            self.refresh_bean_lab()
        except Exception as exc:
            QMessageBox.warning(self, "BEAN Strategy School", str(exc))

    def selected_network(self) -> str | None:
        if self.execution.currentIndex() == 1:
            return "base-sepolia"
        if self.execution.currentIndex() == 2:
            return "base"
        if self.execution.currentIndex() == 3:
            return "advanced"
        return None

    def configure_coinbase(self) -> None:
        key_file, _ = QFileDialog.getOpenFileName(
            self,
            ("Select Coinbase Advanced Trade API key JSON"
             if self.selected_network() == "advanced"
             else "Select Coinbase CDP API key JSON"),
            str(Path.home()),
            "JSON files (*.json);;All files (*.*)",
        )
        if not key_file:
            return

        try:
            data = json.loads(Path(key_file).read_text(encoding="utf-8"))
            api_key_id = str(data.get("id") or data.get("name") or "").strip()
            api_key_secret = str(
                data.get("privateKey") or data.get("private_key") or ""
            ).strip()
            if not api_key_id or not api_key_secret:
                raise ValueError("CDP key JSON is missing id or privateKey.")
        except Exception as exc:
            QMessageBox.critical(self, "Coinbase setup", str(exc))
            return

        if self.selected_network() == "advanced":
            try:
                AdvancedTradeSpot.validate_credentials(
                    api_key_id, api_key_secret)
                AdvancedTradeVault().save(api_key_id, api_key_secret)
            except Exception as exc:
                QMessageBox.critical(self, "Coinbase Advanced setup", str(exc))
                return
            QMessageBox.information(
                self, "Coinbase Advanced connected",
                "Advanced Spot authenticated with view and trade permissions.")
            return

        wallet_secret, ok = QInputDialog.getText(
            self,
            "Coinbase Wallet Secret",
            "Wallet Secret:",
            QLineEdit.Password,
        )
        if not ok or not wallet_secret.strip():
            return

        try:
            vault = CredentialVault()
            vault.save(api_key_id, api_key_secret, wallet_secret)
            network = self.selected_network() or "base-sepolia"
            address = asyncio.run(
                CdpWallet(vault=vault, network=network).ensure_account()
            )
        except Exception as exc:
            QMessageBox.critical(self, "Coinbase setup", str(exc))
            return

        QMessageBox.information(
            self,
            "Coinbase connected",
            f"AIscend smart account ready.\n\n{address}",
        )

    def execution_changed(self) -> None:
        if self.timer.isActive():
            return

        network = self.selected_network()
        if network is None:
            self._switch_to_synthetic()
            return

        vault_ready = (AdvancedTradeVault().configured()
                       if network == "advanced"
                       else CredentialVault().configured())
        if not vault_ready:
            # Give the user a working way to provision the chosen rail:
            # previously this reverted to synthetic BEFORE Configure Coinbase
            # could learn which key type the user intended to supply.
            self.configure_coinbase()
            vault_ready = (AdvancedTradeVault().configured()
                           if network == "advanced"
                           else CredentialVault().configured())
        if not vault_ready:
            self.execution.blockSignals(True)
            self.execution.setCurrentIndex(0)
            self.execution.blockSignals(False)
            self._switch_to_synthetic()
            return

        self._switch_to_live(network)

    def _switch_to_synthetic(self) -> None:
        new_store = StateStore()
        new_engine = Engine(new_store)
        old_store = self.store
        self.store = new_store
        self.engine = new_engine
        if old_store is not new_store:
            old_store.close()

        self.subtitle.setText("Autonomous capital experiment · synthetic sandbox")
        self.reset_button.setEnabled(True)
        self.mode.setEnabled(True)
        self.toggle_ai_fields()
        self.refresh()

    def _switch_to_live(self, network: str) -> None:
        if network == "advanced":
            new_store = StateStore(app_data_dir() / "live-advanced.db")
            new_engine = AdvancedSpotEngine(
                store=new_store, rail=AdvancedTradeSpot(),
                growth_gate=BeanAdaptiveGrowthGate(),
            )
        else:
            new_store = StateStore(app_data_dir() / f"live-{network}.db")
            new_engine = LiveEngine(
                store=new_store, wallet=CdpWallet(network=network),
                growth_gate=BeanAdaptiveGrowthGate(),
            )
        try:
            snap = new_engine.sync()
        except Exception as exc:
            new_store.close()
            QMessageBox.critical(self, "Coinbase live mode", str(exc))
            self.execution.blockSignals(True)
            self.execution.setCurrentIndex(0)
            self.execution.blockSignals(False)
            self._switch_to_synthetic()
            return

        old_store = self.store
        self.store = new_store
        self.engine = new_engine
        old_store.close()

        label = ("Advanced Spot" if network == "advanced" else
                 "Base Sepolia" if network == "base-sepolia" else "Base")
        self.subtitle.setText(
            f"Autonomous capital experiment · Coinbase live · {label} · "
            f"{snap['wallet_address'][:24]}…"
        )
        self.reset_button.setEnabled(False)
        if network == "advanced" and self.interval.value() == 3.0:
            # Advanced evaluates real Coinbase markets / learned five-minute
            # candles. Do not hammer the exchange every 3 seconds by default.
            # The operator can still select a different trading cadence.
            self.interval.setValue(300.0)
        if network == "advanced":
            # An OpenAI-compatible model is not wired into Advanced trading.
            # Never show inert controls as though they make trade decisions.
            self.mode.setCurrentIndex(0)
        self.mode.setEnabled(network != "advanced")
        self.toggle_ai_fields()
        self.refresh()

    def toggle_ai_fields(self) -> None:
        advanced = self.selected_network() == "advanced"
        enabled = self.mode.currentIndex() == 1 and not advanced
        self.endpoint.setEnabled(enabled and not self.timer.isActive())
        self.model.setEnabled(enabled and not self.timer.isActive())

    def configure_decider(self) -> None:
        if isinstance(self.engine, AdvancedSpotEngine):
            # The actual Advanced Spot decision policy is the live
            # price-action + feedback-learned strategy planner.
            return
        if self.mode.currentIndex() == 0:
            self.engine.set_decider(BuiltInDecider())
        else:
            endpoint = self.endpoint.text().strip()
            model = self.model.text().strip()
            if not endpoint or not model:
                raise ValueError("AI endpoint and model are required.")
            self.engine.set_decider(OpenAICompatibleDecider(endpoint, model))

    def start_autonomy(self) -> None:
        try:
            self.configure_decider()
        except Exception as exc:
            QMessageBox.critical(self, "Configuration error", str(exc))
            return

        self.timer.setInterval(int(self.interval.value() * 1000))
        self.timer.start()
        self.status.setText("RUNNING")
        self.status.setObjectName("statusRunning")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.reset_button.setEnabled(False)
        self.interval.setEnabled(False)
        self.execution.setEnabled(False)
        self.coinbase_button.setEnabled(False)
        self.mode.setEnabled(False)
        self.endpoint.setEnabled(False)
        self.model.setEnabled(False)

        self.run_cycle()

    def stop_autonomy(self) -> None:
        self.timer.stop()
        self.status.setText("STOPPED")
        self.status.setObjectName("statusStopped")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.interval.setEnabled(True)
        self.execution.setEnabled(True)
        self.coinbase_button.setEnabled(True)
        self.mode.setEnabled(self.selected_network() != "advanced")
        self.reset_button.setEnabled(self.selected_network() is None)
        self.toggle_ai_fields()

    def run_cycle(self) -> None:
        try:
            self.engine.step()
            self.refresh()
        except Exception as exc:
            self.stop_autonomy()
            QMessageBox.critical(
                self,
                "Autonomy halted",
                f"The decision cycle failed and was stopped:\n\n{exc}",
            )

    def reset_experiment(self) -> None:
        if self.selected_network() is not None:
            return
        answer = QMessageBox.question(
            self,
            "Reset experiment",
            "Erase the synthetic bankroll, positions, and journal and restart at $10.00?",
        )
        if answer != QMessageBox.Yes:
            return
        self.store.reset(10.0)
        self.engine = Engine(self.store)
        self.bankroll.setValue(10.0)
        self.refresh()

    def refresh(self) -> None:
        snap = self.engine.snapshot()
        self.net_card.value.setText(f"${snap['net_liquidation']:.4f}")
        self.cash_card.value.setText(f"${snap['cash']:.4f}")
        verified_profit = snap.get("pnl_is_verified_trading_profit", True)
        self.pnl_card.caption.setText(
            "PROFIT / LOSS" if verified_profit else "EQUITY CHANGE*")
        self.pnl_card.caption.setToolTip(
            "" if verified_profit else
            "Change since the first observed account balance; deposits, "
            "withdrawals and transfers are not reconciled. Not trading profit.")
        self.pnl_card.value.setText(f"${snap['pnl']:+.4f}")
        self.multiple_card.value.setText(f"{snap['multiple']:.3f}x")
        self.bankroll.setValue(max(0.0, float(snap["starting_cash"])))

        pos = snap["positions"]
        prices = snap["prices"]
        self.positions.setRowCount(len(pos))
        for row, (symbol, data) in enumerate(sorted(pos.items())):
            values = [
                symbol,
                f"{data['qty']:.8f}",
                (f"${float(data['avg_cost']):.4f}"
                 if data.get("avg_cost") is not None else "Unverified"),
                f"${prices.get(symbol, 0):.4f}",
                f"${data['qty'] * prices.get(symbol, 0):.4f}",
            ]
            for col, value in enumerate(values):
                self.positions.setItem(row, col, QTableWidgetItem(value))

        rows = self.store.latest_decisions(40)
        self.decisions.setRowCount(len(rows))
        for row_index, item in enumerate(rows):
            when = datetime.fromtimestamp(float(item["ts"])).strftime("%H:%M:%S")
            values = [
                when,
                str(item["action"]),
                str(item["symbol"] or ""),
                f"{float(item['fraction']):.2%}",
                str(item["status"]),
                str(item["rationale"]),
            ]
            for col, value in enumerate(values):
                self.decisions.setItem(row_index, col, QTableWidgetItem(value))

        self.positions.resizeColumnsToContents()
        self.decisions.resizeColumnsToContents()
        self.decisions.horizontalHeader().setStretchLastSection(True)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.timer.stop()
        self.bean_timer.stop()
        self.store.close()
        super().closeEvent(event)

    def apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #0f1115;
                color: #e8ebf0;
                font-family: "Segoe UI";
                font-size: 10pt;
            }
            QLabel#title {
                font-size: 24pt;
                font-weight: 700;
            }
            QLabel#subtitle, QLabel#hint {
                color: #8d96a5;
            }
            QLabel#sectionTitle, QLabel#statCaption {
                color: #9099a8;
                font-weight: 700;
                letter-spacing: 1px;
            }
            QLabel#statValue {
                font-size: 19pt;
                font-weight: 700;
            }
            QFrame#statCard, QFrame#panel {
                background: #171a20;
                border: 1px solid #272c35;
                border-radius: 9px;
            }
            QLabel#statusStopped, QLabel#statusRunning {
                padding: 7px 12px;
                border-radius: 7px;
                font-weight: 700;
            }
            QLabel#statusStopped {
                background: #2a2d33;
                color: #b3bac6;
            }
            QLabel#statusRunning {
                background: #163525;
                color: #8ee2ad;
            }
            QPushButton {
                background: #222730;
                border: 1px solid #333a46;
                border-radius: 6px;
                padding: 9px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #2b313c;
            }
            QPushButton:disabled {
                color: #5e6672;
                background: #181b20;
            }
            QPushButton#primaryButton {
                background: #205d43;
                border-color: #2a7958;
            }
            QPushButton#primaryButton:hover {
                background: #287353;
            }
            QPushButton#dangerButton {
                color: #eab0b0;
            }
            QLineEdit, QComboBox, QDoubleSpinBox {
                background: #111419;
                border: 1px solid #303641;
                border-radius: 5px;
                padding: 6px;
            }
            QTableWidget {
                background: #14171c;
                alternate-background-color: #171b21;
                border: 1px solid #272c35;
                gridline-color: #242932;
                border-radius: 6px;
            }
            QHeaderView::section {
                background: #1d2128;
                color: #aeb6c3;
                border: none;
                border-right: 1px solid #2a3039;
                padding: 7px;
                font-weight: 600;
            }
            QLabel#boundary {
                background: #111419;
                border: 1px solid #292f38;
                border-radius: 6px;
                padding: 10px;
                color: #aeb6c3;
            }
            """
        )


def run_app() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    return app.exec()
