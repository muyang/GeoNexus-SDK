package com.mogan.earth.geomcp;

import com.fasterxml.jackson.annotation.JsonInclude;

/**
 * 标准 JSON-RPC 2.0 响应信封。
 * 成功时 result 非 null，失败时 error 非 null。
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public class JsonRpcResponse {

    private String jsonrpc;
    private String id;
    private Object result;
    private JsonRpcError error;

    public JsonRpcResponse() {}

    /** 是否成功（无 error） */
    public boolean isSuccess() {
        return error == null;
    }

    /** 提取 result 并转换为目标类型 */
    @SuppressWarnings("unchecked")
    public <T> T getResultAs(Class<T> type) {
        if (result == null) return null;
        // 实际项目中使用 ObjectMapper.convertValue
        return (T) result;
    }

    // ── getters / setters ──

    public String getJsonrpc() { return jsonrpc; }
    public void setJsonrpc(String jsonrpc) { this.jsonrpc = jsonrpc; }
    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public Object getResult() { return result; }
    public void setResult(Object result) { this.result = result; }
    public JsonRpcError getError() { return error; }
    public void setError(JsonRpcError error) { this.error = error; }
}