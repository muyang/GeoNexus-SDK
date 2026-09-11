package com.mogan.earth.geomcp;

import com.fasterxml.jackson.annotation.JsonInclude;

/**
 * 时间上下文：起止时间 + 采样间隔（ISO 8601）。
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public class TemporalContext {

    /** ISO 8601 开始时间，如 "2015-01-01" */
    private String start;

    /** ISO 8601 结束时间，如 "2015-12-31" */
    private String end;

    /** 采样间隔，如 "P5D"（每 5 天） */
    private String interval;

    public TemporalContext() {}

    public TemporalContext(String start, String end) {
        this.start = start;
        this.end = end;
    }

    public TemporalContext(String start, String end, String interval) {
        this.start = start;
        this.end = end;
        this.interval = interval;
    }

    // ── getters / setters ──

    public String getStart() { return start; }
    public void setStart(String start) { this.start = start; }
    public String getEnd() { return end; }
    public void setEnd(String end) { this.end = end; }
    public String getInterval() { return interval; }
    public void setInterval(String interval) { this.interval = interval; }
}