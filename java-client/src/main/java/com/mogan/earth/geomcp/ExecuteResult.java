package com.mogan.earth.geomcp;

import com.fasterxml.jackson.annotation.JsonInclude;
import java.util.Map;

/**
 * geo.execute 成功响应。
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public class ExecuteResult {

    private String status;
    private String skill;
    private Map<String, Object> outputs;
    private Object geocards;
    private String executedBy;
    private String requestId;

    public ExecuteResult() {}

    public boolean isOk() {
        return "ok".equals(status);
    }

    @SuppressWarnings("unchecked")
    public <T> T getOutput(String key, Class<T> type) {
        if (outputs == null) return null;
        Object val = outputs.get(key);
        if (val == null) return null;
        return (T) val;
    }

    // ── getters / setters ──

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getSkill() { return skill; }
    public void setSkill(String skill) { this.skill = skill; }
    public Map<String, Object> getOutputs() { return outputs; }
    public void setOutputs(Map<String, Object> outputs) { this.outputs = outputs; }
    public Object getGeocards() { return geocards; }
    public void setGeocards(Object geocards) { this.geocards = geocards; }
    public String getExecutedBy() { return executedBy; }
    public void setExecutedBy(String executedBy) { this.executedBy = executedBy; }
    public String getRequestId() { return requestId; }
    public void setRequestId(String requestId) { this.requestId = requestId; }
}