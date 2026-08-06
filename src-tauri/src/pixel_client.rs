use futures_util::future::join_all;
use reqwest::{Method, StatusCode};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::State;
use tokio::sync::Mutex;
use uuid::Uuid;

const SERVER_BASE: &str = "https://lynote.xyz/gpt-api";
const PIXEL_NODES: [&str; 3] = [
    "https://cf.ai-pixel.online",
    "https://api.ai-pixel.online",
    "https://speed.ai-pixel.online",
];
const CACHE_TTL: Duration = Duration::from_secs(300);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(12);
const LONG_TIMEOUT: Duration = Duration::from_secs(120);
const RETRYABLE_ERROR_PREFIX: &str = "__pixel_retryable__:";
const ACCOUNT_STATUSES: [&str; 5] = [
    "",
    "active",
    "codex_quota_protected",
    "rate_limited",
    "error",
];
static RANDOM_NONCE: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PublicTarget {
    id: String,
    email: String,
    connected: bool,
    account_count: Option<u64>,
    last_checked_at: Option<String>,
    error: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct BootstrapSession {
    target_id: String,
    access_token: String,
    refresh_token: String,
    expires_in: u64,
    refresh_path: String,
    error: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct BootstrapResponse {
    revision: String,
    changed: bool,
    targets: Vec<PublicTarget>,
    sessions: Vec<BootstrapSession>,
}

#[derive(Clone)]
struct SessionData {
    access_token: String,
    refresh_token: String,
    refresh_path: String,
    expires_at: Instant,
}

#[derive(Clone)]
struct PreferredNode {
    base_url: String,
    latency_ms: u64,
    valid_until: Instant,
}

#[derive(Default)]
struct ClientData {
    revision: String,
    targets: HashMap<String, PublicTarget>,
    target_order: Vec<String>,
    sessions: HashMap<String, SessionData>,
    preferred: Option<PreferredNode>,
    last_sync: Option<Instant>,
}

pub struct PixelClientState {
    client: reqwest::Client,
    data: Mutex<ClientData>,
}

impl Default for PixelClientState {
    fn default() -> Self {
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(4))
            .timeout(REQUEST_TIMEOUT)
            .build()
            .expect("failed to create Pixel HTTP client");
        Self {
            client,
            data: Mutex::new(ClientData::default()),
        }
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CommandEnvelope {
    data: Value,
    connection_mode: &'static str,
    node: Option<String>,
    latency_ms: Option<u64>,
}

fn manager_key() -> Result<&'static str, String> {
    let key = option_env!("PIXEL_MANAGER_API_KEY").unwrap_or("").trim();
    if key.is_empty() {
        return Err("账号池管理密钥未配置".to_string());
    }
    Ok(key)
}

async fn server_json(
    state: &PixelClientState,
    method: Method,
    path: &str,
    query: &[(String, String)],
    body: Option<&Value>,
) -> Result<Value, String> {
    let mut request = state
        .client
        .request(method, format!("{SERVER_BASE}{path}"))
        .header("Accept", "application/json")
        .header("X-91-Manager-Key", manager_key()?);
    if !query.is_empty() {
        request = request.query(query);
    }
    if let Some(body) = body {
        request = request.json(body);
    }
    let response = request
        .send()
        .await
        .map_err(|_| "服务器连接失败".to_string())?;
    let status = response.status();
    let payload: Value = response.json().await.unwrap_or(Value::Null);
    if !status.is_success() {
        return Err(api_error(
            &payload,
            format!("服务器请求失败（HTTP {status}）"),
        ));
    }
    Ok(payload)
}

fn api_error(payload: &Value, fallback: String) -> String {
    payload
        .get("detail")
        .or_else(|| payload.get("message"))
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .unwrap_or(&fallback)
        .to_string()
}

fn retryable_error(message: impl Into<String>) -> String {
    format!("{RETRYABLE_ERROR_PREFIX}{}", message.into())
}

fn is_retryable_error(message: &str) -> bool {
    message.starts_with(RETRYABLE_ERROR_PREFIX)
}

fn public_direct_error(message: String) -> String {
    message
        .strip_prefix(RETRYABLE_ERROR_PREFIX)
        .unwrap_or(&message)
        .to_string()
}

fn should_retry_status(status: StatusCode) -> bool {
    status == StatusCode::UNAUTHORIZED || status.is_server_error()
}

fn allowed_operation(operation: &str) -> bool {
    matches!(
        operation,
        "targets"
            | "accounts"
            | "accountUsage"
            | "bulkDelete"
            | "bulkTest"
            | "bulkUpdate"
            | "shareAll"
    )
}

fn cached_preferred(preferred: &Option<PreferredNode>, now: Instant) -> Option<PreferredNode> {
    preferred
        .as_ref()
        .filter(|node| node.valid_until > now)
        .cloned()
}

fn fastest_node(nodes: impl IntoIterator<Item = PreferredNode>) -> Option<PreferredNode> {
    nodes.into_iter().min_by_key(|node| node.latency_ms)
}

async fn sync_bootstrap(
    state: &PixelClientState,
    force_targets: Vec<String>,
) -> Result<Vec<PublicTarget>, String> {
    let (revision, has_sessions) = {
        let data = state.data.lock().await;
        (data.revision.clone(), !data.sessions.is_empty())
    };
    let body = json!({
        "revision": if has_sessions { revision } else { String::new() },
        "refreshTargetIds": force_targets,
    });
    let payload = server_json(
        state,
        Method::POST,
        "/pixel-manager/local-bootstrap",
        &[],
        Some(&body),
    )
    .await?;
    let bootstrap: BootstrapResponse =
        serde_json::from_value(payload).map_err(|_| "本机会话数据格式无效".to_string())?;
    let now = Instant::now();
    let mut data = state.data.lock().await;
    if bootstrap.changed {
        data.sessions.clear();
        data.preferred = None;
    }
    data.revision = bootstrap.revision;
    data.target_order = bootstrap
        .targets
        .iter()
        .map(|target| target.id.clone())
        .collect();
    data.targets = bootstrap
        .targets
        .iter()
        .cloned()
        .map(|target| (target.id.clone(), target))
        .collect();
    for session in bootstrap.sessions {
        if session.access_token.is_empty() {
            data.sessions.remove(&session.target_id);
            if let Some(target) = data.targets.get_mut(&session.target_id) {
                target.connected = false;
                target.error = session.error;
            }
            continue;
        }
        data.sessions.insert(
            session.target_id,
            SessionData {
                access_token: session.access_token,
                refresh_token: session.refresh_token,
                refresh_path: session.refresh_path,
                expires_at: now + Duration::from_secs(session.expires_in.saturating_sub(30).max(1)),
            },
        );
    }
    data.last_sync = Some(now);
    Ok(data
        .target_order
        .iter()
        .filter_map(|target_id| data.targets.get(target_id).cloned())
        .collect())
}

async fn ensure_bootstrap(state: &PixelClientState) -> Result<(), String> {
    let should_sync = {
        let data = state.data.lock().await;
        data.sessions.is_empty()
            || data
                .last_sync
                .map(|checked| checked.elapsed() >= CACHE_TTL)
                .unwrap_or(true)
    };
    if should_sync {
        sync_bootstrap(state, vec![]).await?;
    }
    Ok(())
}

async fn probe_one(
    client: reqwest::Client,
    node: &'static str,
    access_token: String,
) -> Option<PreferredNode> {
    let started = Instant::now();
    let response = client
        .get(format!("{node}/api/v1/accounts"))
        .query(&[("page", "1"), ("page_size", "1")])
        .header("Accept", "application/json")
        .header("Authorization", format!("Bearer {access_token}"))
        .header("x-user-ui-request", "1")
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .ok()?;
    if !response.status().is_success() {
        return None;
    }
    Some(PreferredNode {
        base_url: node.to_string(),
        latency_ms: started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64,
        valid_until: Instant::now() + CACHE_TTL,
    })
}

async fn probe_fastest(state: &PixelClientState) -> Result<PreferredNode, String> {
    ensure_bootstrap(state).await.map_err(retryable_error)?;
    let access_token = {
        let data = state.data.lock().await;
        data.target_order
            .iter()
            .find_map(|target_id| data.sessions.get(target_id))
            .map(|session| session.access_token.clone())
            .ok_or_else(|| retryable_error("没有可用的本机会话"))?
    };
    let probes = PIXEL_NODES
        .into_iter()
        .map(|node| probe_one(state.client.clone(), node, access_token.clone()));
    let fastest = fastest_node(join_all(probes).await.into_iter().flatten())
        .ok_or_else(|| retryable_error("本机无法连接 Pixel 节点"))?;
    state.data.lock().await.preferred = Some(fastest.clone());
    Ok(fastest)
}

async fn preferred_node(state: &PixelClientState) -> Result<PreferredNode, String> {
    ensure_bootstrap(state).await.map_err(retryable_error)?;
    if let Some(preferred) = cached_preferred(&state.data.lock().await.preferred, Instant::now()) {
        return Ok(preferred);
    }
    probe_fastest(state).await
}

async fn session_for(state: &PixelClientState, target_id: &str) -> Result<SessionData, String> {
    ensure_bootstrap(state).await.map_err(retryable_error)?;
    let data = state.data.lock().await;
    if !data.targets.contains_key(target_id) {
        return Err("平台账号不存在".to_string());
    }
    data.sessions
        .get(target_id)
        .cloned()
        .ok_or_else(|| retryable_error("平台本机会话不可用"))
}

fn token_from_payload(payload: &Value) -> Option<(String, String, u64)> {
    let data = payload
        .get("data")
        .filter(|value| value.is_object())
        .unwrap_or(payload);
    let access = data
        .get("access_token")
        .or_else(|| data.get("accessToken"))
        .or_else(|| data.get("token"))?
        .as_str()?
        .to_string();
    let refresh = data
        .get("refresh_token")
        .or_else(|| data.get("refreshToken"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let expires = data
        .get("expires_in")
        .or_else(|| data.get("expiresIn"))
        .and_then(Value::as_u64)
        .unwrap_or(3600);
    Some((access, refresh, expires))
}

async fn renew_session(
    state: &PixelClientState,
    target_id: &str,
    node: &str,
) -> Result<SessionData, String> {
    let current = session_for(state, target_id).await?;
    if !current.refresh_token.is_empty() {
        let response = state
            .client
            .post(format!("{node}{}", current.refresh_path))
            .header("Accept", "application/json")
            .header("x-user-ui-request", "1")
            .json(&json!({"refresh_token": current.refresh_token}))
            .send()
            .await;
        if let Ok(response) = response {
            if response.status().is_success() {
                if let Ok(payload) = response.json::<Value>().await {
                    if let Some((access, refresh, expires)) = token_from_payload(&payload) {
                        let updated = SessionData {
                            access_token: access,
                            refresh_token: if refresh.is_empty() {
                                current.refresh_token
                            } else {
                                refresh
                            },
                            refresh_path: current.refresh_path,
                            expires_at: Instant::now()
                                + Duration::from_secs(expires.saturating_sub(30).max(1)),
                        };
                        state
                            .data
                            .lock()
                            .await
                            .sessions
                            .insert(target_id.to_string(), updated.clone());
                        return Ok(updated);
                    }
                }
            }
        }
    }
    sync_bootstrap(state, vec![target_id.to_string()])
        .await
        .map_err(retryable_error)?;
    session_for(state, target_id).await
}

async fn pixel_once(
    state: &PixelClientState,
    node: &str,
    target_id: &str,
    method: Method,
    path: &str,
    query: &[(String, String)],
    body: Option<&Value>,
    timeout: Duration,
) -> Result<Value, String> {
    let mut session = session_for(state, target_id).await?;
    if session.expires_at <= Instant::now() {
        session = renew_session(state, target_id, node).await?;
    }
    for attempt in 0..2 {
        let mut request = state
            .client
            .request(method.clone(), format!("{node}{path}"))
            .header("Accept", "application/json")
            .header("Authorization", format!("Bearer {}", session.access_token))
            .header("x-user-ui-request", "1")
            .timeout(timeout);
        if !query.is_empty() {
            request = request.query(query);
        }
        if let Some(body) = body {
            request = request.json(body);
        }
        let response = request
            .send()
            .await
            .map_err(|_| retryable_error("Pixel 节点连接失败"))?;
        if response.status() == StatusCode::UNAUTHORIZED && attempt == 0 {
            session = renew_session(state, target_id, node).await?;
            continue;
        }
        let status = response.status();
        let payload = response.json::<Value>().await.unwrap_or(Value::Null);
        if status.is_success() {
            return Ok(payload);
        }
        let message = api_error(&payload, format!("Pixel 请求失败（HTTP {status}）"));
        if should_retry_status(status) {
            return Err(retryable_error(message));
        }
        return Err(message);
    }
    Err(retryable_error("Pixel 登录已失效"))
}

async fn pixel_json(
    state: &PixelClientState,
    target_id: &str,
    method: Method,
    path: &str,
    query: &[(String, String)],
    body: Option<&Value>,
    timeout: Duration,
) -> Result<(Value, PreferredNode), String> {
    let preferred = preferred_node(state).await?;
    match pixel_once(
        state,
        &preferred.base_url,
        target_id,
        method.clone(),
        path,
        query,
        body,
        timeout,
    )
    .await
    {
        Ok(payload) => Ok((payload, preferred)),
        Err(error) if is_retryable_error(&error) => {
            state.data.lock().await.preferred = None;
            let retry_node = probe_fastest(state).await?;
            let payload = pixel_once(
                state,
                &retry_node.base_url,
                target_id,
                method,
                path,
                query,
                body,
                timeout,
            )
            .await?;
            Ok((payload, retry_node))
        }
        Err(error) => Err(error),
    }
}

fn value_text(value: Option<&Value>) -> String {
    value
        .and_then(Value::as_str)
        .unwrap_or("")
        .chars()
        .take(1000)
        .collect()
}

fn value_u64(value: Option<&Value>) -> u64 {
    value.and_then(Value::as_u64).unwrap_or(0)
}

fn value_number(value: Option<&Value>) -> Value {
    match value {
        Some(Value::Number(number)) => Value::Number(number.clone()),
        _ => Value::Null,
    }
}

fn first<'a>(object: &'a Map<String, Value>, keys: &[&str]) -> Option<&'a Value> {
    keys.iter().find_map(|key| object.get(*key))
}

fn sanitize_account(value: &Value) -> Option<Value> {
    let account = value.as_object()?;
    let id = account.get("id")?.as_u64()?;
    let groups = account
        .get("groups")
        .and_then(Value::as_array)
        .map(|groups| {
            groups
                .iter()
                .filter_map(|group| {
                    let group = group.as_object()?;
                    Some(json!({
                        "id": value_u64(group.get("id")),
                        "name": value_text(group.get("name")),
                    }))
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    Some(json!({
        "id": id,
        "name": value_text(account.get("name")),
        "platform": value_text(account.get("platform")),
        "accountLevel": value_text(first(account, &["account_level", "accountLevel"])),
        "type": value_text(account.get("type")),
        "shareMode": value_text(first(account, &["share_mode", "shareMode"])),
        "shareStatus": value_text(first(account, &["share_status", "shareStatus"])),
        "concurrency": value_u64(account.get("concurrency")),
        "currentConcurrency": value_u64(first(account, &["current_concurrency", "currentConcurrency"])),
        "priority": value_u64(account.get("priority")),
        "status": value_text(account.get("status")),
        "schedulable": account.get("schedulable").and_then(Value::as_bool).unwrap_or(false),
        "credentialsStatus": value_text(first(account, &["credentials_status", "credentialsStatus"])),
        "errorMessage": value_text(first(account, &["error_message", "errorMessage"])),
        "errorSince": first(account, &["error_since", "errorSince"]).cloned().unwrap_or(Value::Null),
        "expiresAt": first(account, &["expires_at", "expiresAt"]).cloned().unwrap_or(Value::Null),
        "createdAt": first(account, &["created_at", "createdAt"]).cloned().unwrap_or(Value::Null),
        "updatedAt": first(account, &["updated_at", "updatedAt"]).cloned().unwrap_or(Value::Null),
        "codex5hLimitPercent": value_number(first(account, &["codex_5h_limit_percent", "codex5hLimitPercent"])),
        "codex7dLimitPercent": value_number(first(account, &["codex_7d_limit_percent", "codex7dLimitPercent"])),
        "rateLimitedAt": first(account, &["rate_limited_at", "rateLimitedAt"]).cloned().unwrap_or(Value::Null),
        "rateLimitResetAt": first(account, &["rate_limit_reset_at", "rateLimitResetAt"]).cloned().unwrap_or(Value::Null),
        "codexQuotaProtectionReason": first(account, &["codex_quota_protection_reason", "codexQuotaProtectionReason"]).cloned().unwrap_or(Value::Null),
        "codexQuotaProtectionResetAt": first(account, &["codex_quota_protection_reset_at", "codexQuotaProtectionResetAt"]).cloned().unwrap_or(Value::Null),
        "groups": groups,
    }))
}

async fn public_target(state: &PixelClientState, target_id: &str) -> Value {
    state
        .data
        .lock()
        .await
        .targets
        .get(target_id)
        .and_then(|target| serde_json::to_value(target).ok())
        .unwrap_or_else(|| json!({"id": target_id, "email": "", "connected": true, "accountCount": null, "lastCheckedAt": null, "error": null}))
}

async fn list_accounts_direct(
    state: &PixelClientState,
    target_id: &str,
    payload: &Value,
) -> Result<(Value, PreferredNode), String> {
    let page = payload
        .get("page")
        .and_then(Value::as_u64)
        .unwrap_or(1)
        .max(1);
    let page_size = payload
        .get("pageSize")
        .and_then(Value::as_u64)
        .unwrap_or(100)
        .clamp(1, 100);
    let status = payload
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    if !ACCOUNT_STATUSES.contains(&status) {
        return Err("账号状态筛选无效".to_string());
    }
    let search = payload
        .get("search")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    let mut query = vec![
        ("page".to_string(), page.to_string()),
        ("page_size".to_string(), page_size.to_string()),
        ("sort_by".to_string(), "created_at".to_string()),
        ("sort_order".to_string(), "desc".to_string()),
        ("timezone".to_string(), "Asia/Shanghai".to_string()),
    ];
    if !status.is_empty() {
        query.push(("status".to_string(), status.to_string()));
    }
    if !search.is_empty() {
        query.push(("search".to_string(), search.to_string()));
    }
    let (raw, node) = pixel_json(
        state,
        target_id,
        Method::GET,
        "/api/v1/accounts",
        &query,
        None,
        REQUEST_TIMEOUT,
    )
    .await?;
    let data = raw
        .get("data")
        .filter(|value| value.is_object())
        .unwrap_or(&raw);
    if !data.is_object() || !data.get("items").is_some_and(Value::is_array) {
        return Err("平台账号列表格式无效".to_string());
    }
    let items = data
        .get("items")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(sanitize_account)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let total = data
        .get("total")
        .and_then(Value::as_u64)
        .unwrap_or(items.len() as u64);
    let pages = data
        .get("pages")
        .and_then(Value::as_u64)
        .unwrap_or_else(|| {
            if total == 0 {
                0
            } else {
                total.div_ceil(page_size)
            }
        });
    Ok((
        json!({
            "items": items,
            "total": total,
            "page": data.get("page").and_then(Value::as_u64).unwrap_or(page),
            "pageSize": data.get("page_size").and_then(Value::as_u64).unwrap_or(page_size),
            "pages": pages,
            "target": public_target(state, target_id).await,
        }),
        node,
    ))
}

fn account_ids(payload: &Value) -> Result<Vec<u64>, String> {
    let mut seen = HashSet::new();
    let ids = payload
        .get("accountIds")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_u64)
        .filter(|id| *id > 0 && seen.insert(*id))
        .collect::<Vec<_>>();
    if ids.is_empty() || ids.len() > 100 {
        return Err("请选择有效账号".to_string());
    }
    Ok(ids)
}

fn normalize_bulk(payload: &Value, requested: &[u64]) -> Value {
    let data = payload
        .get("data")
        .filter(|value| value.is_object())
        .unwrap_or(payload);
    let requested_set = requested.iter().copied().collect::<HashSet<_>>();
    let successes = data
        .get("success_ids")
        .or_else(|| data.get("successIds"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_u64)
        .filter(|id| requested_set.contains(id))
        .collect::<HashSet<_>>();
    let failures = data
        .get("failed_ids")
        .or_else(|| data.get("failedIds"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_u64)
        .filter(|id| requested_set.contains(id))
        .collect::<HashSet<_>>();
    let reported_success = data.get("success").and_then(Value::as_u64).unwrap_or(0);
    let success_ids = if successes.is_empty()
        && failures.is_empty()
        && reported_success == requested.len() as u64
    {
        requested.to_vec()
    } else {
        requested
            .iter()
            .copied()
            .filter(|id| successes.contains(id))
            .collect()
    };
    let failed_ids = requested
        .iter()
        .copied()
        .filter(|id| !success_ids.contains(id))
        .collect::<Vec<_>>();
    json!({
        "ok": failed_ids.is_empty(),
        "success": success_ids.len(),
        "failed": failed_ids.len(),
        "successIds": success_ids,
        "failedIds": failed_ids,
    })
}

async fn fallback(
    state: &PixelClientState,
    method: Method,
    path: String,
    query: Vec<(String, String)>,
    body: Option<Value>,
) -> Result<CommandEnvelope, String> {
    let data = server_json(state, method, &path, &query, body.as_ref()).await?;
    Ok(CommandEnvelope {
        data,
        connection_mode: "server",
        node: None,
        latency_ms: None,
    })
}

fn direct_envelope(data: Value, node: PreferredNode) -> CommandEnvelope {
    CommandEnvelope {
        data,
        connection_mode: "direct",
        node: Some(node.base_url),
        latency_ms: Some(node.latency_ms),
    }
}

async fn bulk_test_direct(
    state: &PixelClientState,
    target_id: &str,
    ids: &[u64],
) -> Result<(Value, PreferredNode), String> {
    preferred_node(state).await?;
    let mut results = Vec::new();
    for chunk in ids.chunks(3) {
        let futures = chunk.iter().map(|account_id| {
            let path = format!("/api/v1/accounts/{account_id}/test");
            async move {
                let result = pixel_json(
                    state,
                    target_id,
                    Method::POST,
                    &path,
                    &[],
                    Some(&json!({})),
                    LONG_TIMEOUT,
                )
                .await;
                match result {
                    Ok((payload, _)) => {
                        let data = payload.get("data").filter(|value| value.is_object()).unwrap_or(&payload);
                        let status = data.get("status").and_then(Value::as_str).unwrap_or("");
                        Ok(json!({"accountId": account_id, "success": data.get("success").and_then(Value::as_bool).unwrap_or(false) || status == "success"}))
                    }
                    Err(error) if is_retryable_error(&error) => Err(error),
                    Err(_) => Ok(json!({"accountId": account_id, "success": false})),
                }
            }
        });
        for result in join_all(futures).await {
            results.push(result?);
        }
    }
    let success_ids = results
        .iter()
        .filter(|item| {
            item.get("success")
                .and_then(Value::as_bool)
                .unwrap_or(false)
        })
        .filter_map(|item| item.get("accountId").and_then(Value::as_u64))
        .collect::<Vec<_>>();
    let failed_ids = ids
        .iter()
        .copied()
        .filter(|id| !success_ids.contains(id))
        .collect::<Vec<_>>();
    Ok((
        json!({
            "ok": failed_ids.is_empty(),
            "total": ids.len(),
            "success": success_ids.len(),
            "failed": failed_ids.len(),
            "successIds": success_ids,
            "failedIds": failed_ids,
            "results": results,
        }),
        preferred_node(state).await?,
    ))
}

async fn share_target_direct(
    state: &PixelClientState,
    target_id: &str,
    ids: &[u64],
    fixed_concurrency: Option<u64>,
) -> Result<(Value, PreferredNode), String> {
    let concurrency = ids
        .iter()
        .map(|id| {
            (
                *id,
                fixed_concurrency.unwrap_or_else(|| random_concurrency(*id)),
            )
        })
        .collect::<BTreeMap<_, _>>();
    let mut node = preferred_node(state).await?;
    for chunk in ids.chunks(100) {
        let mut grouped: BTreeMap<u64, Vec<u64>> = BTreeMap::new();
        for account_id in chunk {
            grouped
                .entry(concurrency[account_id])
                .or_default()
                .push(*account_id);
        }
        for (value, group_ids) in grouped {
            let body = json!({"account_ids": group_ids, "concurrency": value});
            let (updated, current_node) =
                update_concurrency_direct(state, target_id, &body).await?;
            node = current_node;
            let update_result = normalize_bulk(
                &updated,
                body["account_ids"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .filter_map(Value::as_u64)
                    .collect::<Vec<_>>()
                    .as_slice(),
            );
            let success_ids = update_result["successIds"]
                .as_array()
                .cloned()
                .unwrap_or_default();
            let share_ids = success_ids
                .iter()
                .filter_map(Value::as_u64)
                .collect::<Vec<_>>();
            if share_ids.is_empty() {
                continue;
            }
            let share_body = json!({
                "account_ids": share_ids,
                "target": "public_pool",
                "idempotency_key": Uuid::new_v4().to_string(),
            });
            let (_, current_node) = pixel_json(
                state,
                target_id,
                Method::POST,
                "/api/v1/accounts/external-placement:convert-batch",
                &[],
                Some(&share_body),
                LONG_TIMEOUT,
            )
            .await?;
            node = current_node;
        }
    }
    let mut verified = HashSet::new();
    for attempt in 0..3 {
        verified.clear();
        for page in 1..=200u64 {
            let (listed, current_node) =
                list_accounts_direct(state, target_id, &json!({"page": page, "pageSize": 100}))
                    .await?;
            node = current_node;
            for item in listed["items"].as_array().into_iter().flatten() {
                let id = item.get("id").and_then(Value::as_u64).unwrap_or(0);
                let is_public = item.get("shareMode").and_then(Value::as_str) == Some("public");
                let actual = item.get("concurrency").and_then(Value::as_u64).unwrap_or(0);
                if concurrency.get(&id) == Some(&actual) && is_public {
                    verified.insert(id);
                }
            }
            if page >= listed.get("pages").and_then(Value::as_u64).unwrap_or(page) {
                break;
            }
        }
        if verified.len() == ids.len() || attempt == 2 {
            break;
        }
        tokio::time::sleep(Duration::from_millis(300)).await;
    }
    let success_ids = ids
        .iter()
        .copied()
        .filter(|id| verified.contains(id))
        .collect::<Vec<_>>();
    let failed_ids = ids
        .iter()
        .copied()
        .filter(|id| !verified.contains(id))
        .collect::<Vec<_>>();
    let concurrency_by_id = success_ids
        .iter()
        .map(|id| (id.to_string(), json!(concurrency[id])))
        .collect::<Map<_, _>>();
    Ok((
        json!({
            "ok": failed_ids.is_empty(),
            "success": success_ids.len(),
            "failed": failed_ids.len(),
            "successIds": success_ids,
            "failedIds": failed_ids,
            "concurrencyById": concurrency_by_id,
        }),
        node,
    ))
}

async fn update_concurrency_direct(
    state: &PixelClientState,
    target_id: &str,
    body: &Value,
) -> Result<(Value, PreferredNode), String> {
    match pixel_json(
        state,
        target_id,
        Method::POST,
        "/api/v1/accounts/bulk-update",
        &[],
        Some(body),
        LONG_TIMEOUT,
    )
    .await
    {
        Ok(result) => Ok(result),
        Err(error) if is_retryable_error(&error) => Err(error),
        Err(_) => {
            pixel_json(
                state,
                target_id,
                Method::POST,
                "/api/v1/admin/accounts/bulk-update",
                &[],
                Some(body),
                LONG_TIMEOUT,
            )
            .await
        }
    }
}

fn random_concurrency(account_id: u64) -> u64 {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64;
    let nonce = RANDOM_NONCE.fetch_add(1, Ordering::Relaxed);
    3 + (nanos ^ account_id.rotate_left(17) ^ nonce.wrapping_mul(0x9E37_79B9)) % 8
}

async fn share_all_direct(
    state: &PixelClientState,
    payload: &Value,
) -> Result<(Value, PreferredNode), String> {
    ensure_bootstrap(state).await?;
    let requested_ids = payload
        .get("targetIds")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .collect::<Vec<_>>();
    let (target_ids, targets) = {
        let data = state.data.lock().await;
        let target_ids = if requested_ids.is_empty() {
            data.target_order.clone()
        } else {
            requested_ids
        };
        for target_id in &target_ids {
            if !data.targets.contains_key(target_id) {
                return Err("平台账号不存在".to_string());
            }
        }
        (target_ids, data.targets.clone())
    };
    if target_ids.is_empty() {
        return Err("没有可处理的平台账号".to_string());
    }

    let fixed_concurrency = payload.get("concurrency").and_then(Value::as_u64);
    let mut results = Vec::with_capacity(target_ids.len());
    let mut latest_node = preferred_node(state).await?;
    for target_id in target_ids {
        let target = targets
            .get(&target_id)
            .ok_or_else(|| "平台账号不存在".to_string())?;
        let mut ids = Vec::new();
        for page in 1..=200u64 {
            let (listed, node) =
                list_accounts_direct(state, &target_id, &json!({"page": page, "pageSize": 100}))
                    .await?;
            latest_node = node;
            ids.extend(
                listed["items"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|item| item.get("id").and_then(Value::as_u64)),
            );
            if page >= listed.get("pages").and_then(Value::as_u64).unwrap_or(page) {
                break;
            }
        }
        ids.sort_unstable();
        ids.dedup();
        if ids.is_empty() {
            results.push(json!({
                "targetId": target_id,
                "email": target.email,
                "total": 0,
                "shared": 0,
                "failed": 0,
                "failedIds": [],
                "concurrencyById": {},
                "status": "success",
                "message": "平台没有账号",
            }));
            continue;
        }
        let total = ids.len();
        let (shared, node) =
            share_target_direct(state, &target_id, &ids, fixed_concurrency).await?;
        latest_node = node;
        let success = shared.get("success").and_then(Value::as_u64).unwrap_or(0);
        let failed = shared
            .get("failed")
            .and_then(Value::as_u64)
            .unwrap_or(total as u64);
        let status = if failed == 0 {
            "success"
        } else if success == 0 {
            "failed"
        } else {
            "partial"
        };
        results.push(json!({
            "targetId": target_id,
            "email": target.email,
            "total": total,
            "shared": success,
            "failed": failed,
            "failedIds": shared.get("failedIds").cloned().unwrap_or_else(|| json!([])),
            "concurrencyById": shared.get("concurrencyById").cloned().unwrap_or_else(|| json!({})),
            "status": status,
            "message": if failed == 0 { "公共共享已全部开启".to_string() } else { format!("仍有 {failed} 个账号共享失败") },
        }));
    }
    let total = results
        .iter()
        .map(|item| value_u64(item.get("total")))
        .sum::<u64>();
    let shared = results
        .iter()
        .map(|item| value_u64(item.get("shared")))
        .sum::<u64>();
    let failed = results
        .iter()
        .map(|item| value_u64(item.get("failed")))
        .sum::<u64>();
    let failed_targets = results
        .iter()
        .filter(|item| item.get("status").and_then(Value::as_str) != Some("success"))
        .count();
    let status = if failed_targets == 0 {
        "success"
    } else if failed_targets == results.len() {
        "failed"
    } else {
        "partial"
    };
    Ok((
        json!({
            "ok": status == "success",
            "status": status,
            "totalTargets": results.len(),
            "total": total,
            "shared": shared,
            "failed": failed,
            "results": results,
            "message": if status == "success" { "全部平台公共共享已全部开启".to_string() } else { format!("公共共享完成，但有 {failed_targets} 个平台存在失败") },
        }),
        latest_node,
    ))
}

#[tauri::command(rename_all = "camelCase")]
pub async fn pixel_manager_request(
    operation: String,
    target_id: Option<String>,
    payload: Option<Value>,
    state: State<'_, PixelClientState>,
) -> Result<CommandEnvelope, String> {
    if !allowed_operation(&operation) {
        return Err("不支持的本机 Pixel 操作".to_string());
    }
    let payload = payload.unwrap_or_else(|| json!({}));
    if operation == "targets" {
        match sync_bootstrap(&state, vec![]).await {
            Ok(targets) => match preferred_node(&state).await {
                Ok(node) => return Ok(direct_envelope(json!({"targets": targets}), node)),
                Err(_) => {
                    return fallback(
                        &state,
                        Method::GET,
                        "/pixel-manager/targets".to_string(),
                        vec![],
                        None,
                    )
                    .await;
                }
            },
            Err(_) => {
                return fallback(
                    &state,
                    Method::GET,
                    "/pixel-manager/targets".to_string(),
                    vec![],
                    None,
                )
                .await
            }
        }
    }
    if operation == "shareAll" {
        return match share_all_direct(&state, &payload).await {
            Ok((data, node)) => Ok(direct_envelope(data, node)),
            Err(error) if !is_retryable_error(&error) => Err(public_direct_error(error)),
            Err(_) => {
                fallback(
                    &state,
                    Method::POST,
                    "/pixel-manager/share-all".to_string(),
                    vec![],
                    Some(payload),
                )
                .await
            }
        };
    }
    let target_id = target_id
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "缺少平台账号".to_string())?;
    match operation.as_str() {
        "accounts" => match list_accounts_direct(&state, &target_id, &payload).await {
            Ok((data, node)) => Ok(direct_envelope(data, node)),
            Err(error) if !is_retryable_error(&error) => Err(public_direct_error(error)),
            Err(_) => {
                let query = vec![
                    (
                        "page".to_string(),
                        payload
                            .get("page")
                            .and_then(Value::as_u64)
                            .unwrap_or(1)
                            .to_string(),
                    ),
                    (
                        "pageSize".to_string(),
                        payload
                            .get("pageSize")
                            .and_then(Value::as_u64)
                            .unwrap_or(100)
                            .to_string(),
                    ),
                    (
                        "status".to_string(),
                        payload
                            .get("status")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                    ),
                    (
                        "search".to_string(),
                        payload
                            .get("search")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                    ),
                ];
                fallback(
                    &state,
                    Method::GET,
                    format!("/pixel-manager/targets/{target_id}/accounts"),
                    query,
                    None,
                )
                .await
            }
        },
        "accountUsage" => {
            let account_id = payload
                .get("accountId")
                .and_then(Value::as_u64)
                .ok_or_else(|| "账号 ID 无效".to_string())?;
            let path = format!("/api/v1/accounts/{account_id}/usage");
            match pixel_json(
                &state,
                &target_id,
                Method::GET,
                &path,
                &[("source".to_string(), "local".to_string())],
                None,
                REQUEST_TIMEOUT,
            )
            .await
            {
                Ok((raw, node)) => {
                    let data = raw
                        .get("data")
                        .filter(|value| value.is_object())
                        .unwrap_or(&raw);
                    let window = |key: &str| {
                        data.get(key)
                            .and_then(|value| value.get("utilization"))
                            .cloned()
                            .unwrap_or(Value::Null)
                    };
                    Ok(direct_envelope(
                        json!({"accountId": account_id, "codex5hLimitPercent": window("five_hour"), "codex7dLimitPercent": window("seven_day")}),
                        node,
                    ))
                }
                Err(error) if !is_retryable_error(&error) => Err(public_direct_error(error)),
                Err(_) => {
                    fallback(
                        &state,
                        Method::GET,
                        format!("/pixel-manager/targets/{target_id}/accounts/{account_id}/usage"),
                        vec![],
                        None,
                    )
                    .await
                }
            }
        }
        "bulkDelete" => {
            let ids = account_ids(&payload)?;
            let direct_body = json!({"account_ids": ids});
            match pixel_json(
                &state,
                &target_id,
                Method::POST,
                "/api/v1/accounts/bulk-delete",
                &[],
                Some(&direct_body),
                LONG_TIMEOUT,
            )
            .await
            {
                Ok((raw, node)) => Ok(direct_envelope(normalize_bulk(&raw, &ids), node)),
                Err(error) if !is_retryable_error(&error) => Err(public_direct_error(error)),
                Err(_) => {
                    fallback(
                        &state,
                        Method::POST,
                        format!("/pixel-manager/targets/{target_id}/accounts/bulk-delete"),
                        vec![],
                        Some(json!({"accountIds": ids})),
                    )
                    .await
                }
            }
        }
        "bulkTest" => {
            let ids = account_ids(&payload)?;
            match bulk_test_direct(&state, &target_id, &ids).await {
                Ok((data, node)) => Ok(direct_envelope(data, node)),
                Err(error) if !is_retryable_error(&error) => Err(public_direct_error(error)),
                Err(_) => {
                    fallback(
                        &state,
                        Method::POST,
                        format!("/pixel-manager/targets/{target_id}/accounts/bulk-test"),
                        vec![],
                        Some(json!({"accountIds": ids})),
                    )
                    .await
                }
            }
        }
        "bulkUpdate" => {
            let ids = account_ids(&payload)?;
            let make_public = payload
                .get("makePublic")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let concurrency = payload.get("concurrency").and_then(Value::as_u64);
            if make_public {
                match share_target_direct(&state, &target_id, &ids, concurrency).await {
                    Ok((data, node)) => Ok(direct_envelope(data, node)),
                    Err(error) if !is_retryable_error(&error) => Err(public_direct_error(error)),
                    Err(_) => {
                        fallback(
                            &state,
                            Method::POST,
                            format!("/pixel-manager/targets/{target_id}/accounts/bulk-update"),
                            vec![],
                            Some(payload),
                        )
                        .await
                    }
                }
            } else {
                let direct_body = json!({"account_ids": ids, "concurrency": concurrency});
                match pixel_json(
                    &state,
                    &target_id,
                    Method::POST,
                    "/api/v1/accounts/bulk-update",
                    &[],
                    Some(&direct_body),
                    LONG_TIMEOUT,
                )
                .await
                {
                    Ok((raw, node)) => Ok(direct_envelope(normalize_bulk(&raw, &ids), node)),
                    Err(error) if !is_retryable_error(&error) => Err(public_direct_error(error)),
                    Err(_) => {
                        fallback(
                            &state,
                            Method::POST,
                            format!("/pixel-manager/targets/{target_id}/accounts/bulk-update"),
                            vec![],
                            Some(payload),
                        )
                        .await
                    }
                }
            }
        }
        _ => Err("不支持的本机 Pixel 操作".to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn account_sanitizer_drops_credentials_and_extra() {
        let raw = json!({
            "id": 7,
            "name": "visible@example.com",
            "status": "active",
            "credentials": {"access_token": "secret"},
            "extra": {"refresh_token": "refresh-secret"},
        });
        let sanitized = sanitize_account(&raw).unwrap();
        let encoded = sanitized.to_string();
        assert!(encoded.contains("visible@example.com"));
        assert!(!encoded.contains("secret"));
        assert!(sanitized.get("credentials").is_none());
        assert!(sanitized.get("extra").is_none());
    }

    #[test]
    fn only_allowlisted_operations_can_cross_ipc() {
        for operation in [
            "targets",
            "accounts",
            "accountUsage",
            "bulkDelete",
            "bulkTest",
            "bulkUpdate",
            "shareAll",
        ] {
            assert!(allowed_operation(operation));
        }
        for operation in ["rawRequest", "export", "import", "getToken", "getPassword"] {
            assert!(!allowed_operation(operation));
        }
    }

    #[test]
    fn only_auth_network_and_server_failures_are_retryable() {
        assert!(should_retry_status(StatusCode::UNAUTHORIZED));
        assert!(should_retry_status(StatusCode::BAD_GATEWAY));
        assert!(should_retry_status(StatusCode::INTERNAL_SERVER_ERROR));
        assert!(!should_retry_status(StatusCode::BAD_REQUEST));
        assert!(!should_retry_status(StatusCode::NOT_FOUND));
        assert!(is_retryable_error(&retryable_error("network")));
        assert!(!is_retryable_error("validation failed"));
    }

    #[test]
    fn fastest_node_and_cache_expiration_are_deterministic() {
        let now = Instant::now();
        let fast = PreferredNode {
            base_url: PIXEL_NODES[1].to_string(),
            latency_ms: 15,
            valid_until: now + CACHE_TTL,
        };
        let slow = PreferredNode {
            base_url: PIXEL_NODES[0].to_string(),
            latency_ms: 80,
            valid_until: now + CACHE_TTL,
        };
        assert_eq!(
            fastest_node([slow, fast.clone()]).unwrap().base_url,
            PIXEL_NODES[1]
        );
        assert!(cached_preferred(&Some(fast.clone()), now).is_some());
        assert!(cached_preferred(&Some(fast), now + CACHE_TTL).is_none());
    }
}
