// -------------------------------------------------------------------------------------------------
//  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
//  https://nautechsystems.io
//
//  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
//  You may not use this file except in compliance with the License.
//  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
//
//  Unless required by applicable law or agreed to in writing, software
//  distributed under the License is distributed on an "AS IS" BASIS,
//  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//  See the License for the specific language governing permissions and
//  limitations under the License.
// -------------------------------------------------------------------------------------------------

//! Python bindings for the [`RiskEngine`].

use std::{cell::RefCell, rc::Rc};

use nautilus_model::enums::TradingState;
use pyo3::prelude::*;

use crate::engine::RiskEngine;

/// Python handle to a live [`RiskEngine`].
///
/// Holds the same `Rc<RefCell<RiskEngine>>` the kernel and its components share, so a
/// state change made through this handle is seen by the engine that gates order flow.
///
/// Obtain it from `LiveNode.risk_engine` **before** starting a hosted run. The run takes
/// ownership of the node, so the node's own accessors stop resolving once it is under
/// way, while a handle taken beforehand keeps working - the same lifetime the `cache`
/// and `portfolio` handles have.
///
/// `unsendable` is load-bearing rather than incidental. The message bus is thread-local
/// and `get_message_bus` *creates a fresh default bus* when a thread has none, so a call
/// from the wrong thread would set the state correctly and publish the
/// `TradingStateChanged` event into a throwaway bus nobody is subscribed to - a halt that
/// takes effect silently, which is the worst of both outcomes. PyO3 raises instead.
#[allow(non_camel_case_types)]
#[pyo3::pyclass(
    module = "nautilus_trader.risk",
    name = "RiskEngine",
    unsendable,
    from_py_object
)]
#[pyo3_stub_gen::derive::gen_stub_pyclass(module = "nautilus_trader.risk")]
#[derive(Debug, Clone)]
pub struct PyRiskEngine(Rc<RefCell<RiskEngine>>);

impl PyRiskEngine {
    /// Creates a `PyRiskEngine` from an `Rc<RefCell<RiskEngine>>`.
    #[must_use]
    pub fn from_rc(rc: Rc<RefCell<RiskEngine>>) -> Self {
        Self(rc)
    }

    /// Gets the inner `Rc<RefCell<RiskEngine>>` for use in Rust code.
    #[must_use]
    pub fn engine_rc(&self) -> Rc<RefCell<RiskEngine>> {
        self.0.clone()
    }
}

#[pyo3_stub_gen::derive::gen_stub_pymethods]
#[pymethods]
impl PyRiskEngine {
    /// Returns the current trading state.
    #[getter]
    #[pyo3(name = "trading_state")]
    fn py_trading_state(&self) -> TradingState {
        self.0.borrow().trading_state()
    }

    /// Sets the trading state, gating order flow at the engine.
    ///
    /// `HALTED` denies every new order and `REDUCING` denies any that would increase
    /// exposure, both enforced inside the engine rather than by the caller. Setting the
    /// state it already holds is a no-op that logs a warning.
    ///
    /// Publishes a `TradingStateChanged` event on `events.risk`.
    #[pyo3(name = "set_trading_state")]
    fn py_set_trading_state(&self, state: TradingState) {
        self.0.borrow_mut().set_trading_state(state);
    }

    fn __repr__(&self) -> String {
        format!(
            "RiskEngine(trading_state={:?})",
            self.0.borrow().trading_state()
        )
    }
}
