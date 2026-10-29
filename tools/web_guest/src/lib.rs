//! The fixed link's browser end: iroh compiled to WebAssembly, as thin as it
//! can be. It dials a relay by its endpoint ID over n0's relays (a browser
//! can't send UDP, so it never goes direct) and opens one bidirectional
//! stream that carries length-prefixed frames. What the frames say is the
//! page's business: they are the guest websocket's JSON messages, unchanged
//! (vrc_parameter_relay/web/app.js and fixed_link.py).

use std::rc::Rc;

use iroh::{
    Endpoint, EndpointId, SecretKey,
    endpoint::{Connection, RecvStream, SendStream, VarInt, presets},
};
use js_sys::{Promise, Uint8Array};
use tokio::sync::Mutex;
use wasm_bindgen::prelude::*;
use wasm_bindgen_futures::future_to_promise;

/// The biggest frame the page accepts — a YOLO hello with every parameter of
/// a large avatar is well under this.
const MAX_FRAME: usize = 4 * 1024 * 1024;

#[wasm_bindgen(start)]
fn start() {
    console_error_panic_hook::set_once();
}

fn js_err(e: impl std::fmt::Display) -> JsValue {
    JsError::new(&e.to_string()).into()
}

/// This browser's endpoint.
#[wasm_bindgen]
pub struct Guest {
    ep: Endpoint,
}

#[wasm_bindgen]
impl Guest {
    /// Bind an endpoint with `key`, a 32-byte secret the page makes.
    pub async fn spawn(key: Vec<u8>) -> Result<Guest, JsError> {
        let bytes: [u8; 32] = key
            .as_slice()
            .try_into()
            .map_err(|_| JsError::new("the key must be 32 bytes"))?;
        let ep = Endpoint::builder(presets::N0)
            .secret_key(SecretKey::from_bytes(&bytes))
            .bind()
            .await
            .map_err(|e| JsError::new(&e.to_string()))?;
        Ok(Guest { ep })
    }

    pub fn id(&self) -> String {
        self.ep.id().to_string()
    }

    /// Dial `peer` (64 hex digits) for `alpn` and open the stream; resolves
    /// to a `Pipe`.
    pub fn dial(&self, peer: String, alpn: String) -> Promise {
        let ep = self.ep.clone();
        future_to_promise(async move {
            let id: EndpointId = peer.parse().map_err(js_err)?;
            let conn = ep.connect(id, alpn.as_bytes()).await.map_err(js_err)?;
            let (send, recv) = conn.open_bi().await.map_err(js_err)?;
            Ok(JsValue::from(Pipe {
                conn,
                send: Rc::new(Mutex::new(send)),
                recv: Rc::new(Mutex::new(recv)),
            }))
        })
    }

    pub fn close(&self) -> Promise {
        let ep = self.ep.clone();
        future_to_promise(async move {
            ep.close().await;
            Ok(JsValue::UNDEFINED)
        })
    }
}

/// One connection with its stream: whole frames in and out.
#[wasm_bindgen]
pub struct Pipe {
    conn: Connection,
    send: Rc<Mutex<SendStream>>,
    recv: Rc<Mutex<RecvStream>>,
}

#[wasm_bindgen]
impl Pipe {
    /// Send one frame (a 4-byte big-endian length, then `data`).
    pub fn send(&self, data: Vec<u8>) -> Promise {
        let send = self.send.clone();
        future_to_promise(async move {
            let mut frame = Vec::with_capacity(4 + data.len());
            frame.extend_from_slice(&(data.len() as u32).to_be_bytes());
            frame.extend_from_slice(&data);
            send.lock().await.write_all(&frame).await.map_err(js_err)?;
            Ok(JsValue::UNDEFINED)
        })
    }

    /// The next frame; rejects once the stream or the connection is gone
    /// (the error carries the other side's reason).
    pub fn recv(&self) -> Promise {
        let recv = self.recv.clone();
        let conn = self.conn.clone();
        future_to_promise(async move {
            let mut stream = recv.lock().await;
            let mut head = [0u8; 4];
            if let Err(e) = stream.read_exact(&mut head).await {
                // a closed connection says why; prefer that over "finished early"
                return Err(match conn.close_reason() {
                    Some(reason) => js_err(reason),
                    None => js_err(e),
                });
            }
            let len = u32::from_be_bytes(head) as usize;
            if len > MAX_FRAME {
                return Err(js_err(format!("frame too big ({len} bytes)")));
            }
            let mut body = vec![0u8; len];
            stream.read_exact(&mut body).await.map_err(js_err)?;
            Ok(Uint8Array::from(&body[..]).into())
        })
    }

    pub fn close(&self, code: u32, reason: String) {
        self.conn.close(VarInt::from_u32(code), reason.as_bytes());
    }
}
