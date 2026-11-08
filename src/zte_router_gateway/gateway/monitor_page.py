"""网速监控 + 限速页面。

Vue 3 + Element Plus 全局构建，静态文件全部由网关本地提供，不需要前端打包。
"""

from __future__ import annotations

MONITOR_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>设备网速监控</title>
<link rel="stylesheet" href="/__gw/static/element-plus.css">
<style>
  :root { --el-color-primary: #3a9cf5; }
  html, body { margin: 0; padding: 0; background: #f5f7fa; color: #303133;
    font-family: "Microsoft YaHei", "PingFang SC", "Helvetica Neue", Arial, sans-serif; }
  [v-cloak] { display: none; }
  .top { display: flex; align-items: center; justify-content: space-between; gap: 16px;
         height: 60px; padding: 0 24px; background: #3a9cf5;
         box-shadow: 0 2px 10px rgba(58, 156, 245, .25); }
  .brand { display: flex; align-items: center; gap: 14px; }
  .brand img { height: 28px; width: auto; }
  .brand .name { font-size: 17px; font-weight: 600; color: #fff; }
  .brand .name small { display: block; font-size: 12px; font-weight: 400;
                       color: rgba(255, 255, 255, .75); }
  .top .el-button { color: #fff; }
  .top .el-button:hover { color: #e6f2ff; }
  .main { padding: 18px 24px 40px; }
  .card { margin-bottom: 16px; border-radius: 10px; }
  .card-head { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; }
  .card-title { font-size: 15px; font-weight: 600; }
  .head-row { display: flex; align-items: center; gap: 10px; min-width: 0; }
  .chart-block { padding: 2px 0 6px; }
  .signal { display: inline-flex; align-items: flex-end; gap: 2px; }
  .signal i { display: block; width: 3px; border-radius: 1px; background: #dcdfe6; }
  .signal i:nth-child(1) { height: 5px; }
  .signal i:nth-child(2) { height: 8px; }
  .signal i:nth-child(3) { height: 11px; }
  .signal i:nth-child(4) { height: 14px; }
  .signal em { margin-left: 5px; font-style: normal; font-size: 12px; color: #8a94a6; }
  .signal-4 i:nth-child(-n+4) { background: #5ad8a6; }
  .signal-3 i:nth-child(-n+3) { background: #5ad8a6; }
  .signal-2 i:nth-child(-n+2) { background: #f6bd16; }
  .signal-1 i:nth-child(-n+1) { background: #e8684a; }
  .chart-title { margin-bottom: 4px; font-size: 14px; font-weight: 600; }
  .group-label { flex: none; font-size: 14px; font-weight: 500; color: #5a6472;
                 white-space: nowrap; }
  .spacer { flex: 1; }
  .muted { color: #98a2b3; font-size: 12px; }
  .mono { font-family: Consolas, "SFMono-Regular", monospace; font-size: 12px; color: #8a94a6; }
  .dev-line { display: flex; align-items: center; gap: 6px; }
  .dev-cell { display: flex; align-items: center; gap: 10px; }
  .dev-icon { flex: none; display: block; width: 34px; height: 34px; object-fit: contain; }
  .dev-text { min-width: 0; }
  .dev-name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fav { flex: none; font-size: 15px; line-height: 1; color: #c0c4cc; cursor: pointer; user-select: none; }
  .fav:hover { color: #f6bd16; }
  .fav.active { color: #f6bd16; }
  .chart { width: 100%; height: 340px; }
  .row-actions { display: inline-flex; align-items: center; gap: 8px; }
  .permanent { color: #3a9cf5; font-weight: 500; }
  .expiring { color: #e6a23c; font-weight: 500; }
  .el-table .cell { line-height: 1.4; }
  .sysinfo-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
                  gap: 14px 24px; }
  .sysinfo-label { margin-bottom: 4px; font-size: 12px; color: #98a2b3; }
  .sysinfo-value { font-size: 14px; color: #303133; word-break: break-all; }
  @media (max-width: 900px) {
    .top { padding: 0 14px; }
    .main { padding: 14px 10px 32px; }
    .chart { height: 260px; }
    .card-head { flex-direction: column; align-items: stretch; gap: 10px; }
    .spacer { display: none; }
    .head-row { width: 100%; }
    .head-row > .el-radio-group { display: flex; flex: 1 1 auto; min-width: 0; }
    .head-row .el-radio-button { flex: 1 1 0; }
    .head-row .el-radio-button__inner { width: 100%; padding: 8px 4px; font-size: 12px; }
  }
</style>
</head>
<body>
<div id="app" v-cloak>
  <div class="top">
    <div class="brand">
      <img src="/__gw/logo.svg" alt="ZTE">
      <div class="name">设备网速监控<small>ZTE Router Gateway</small></div>
    </div>
    <el-button link type="primary" @click="goHome">返回管理页</el-button>
  </div>

  <div class="main">
    <el-card shadow="never" class="card">
      <template #header>
        <div class="card-head">
          <div class="head-row">
            <span class="group-label">设备筛选</span>
            <el-radio-group v-model="deviceFilter">
              <el-radio-button value="all">全部</el-radio-button>
              <el-radio-button value="favorite">收藏</el-radio-button>
              <el-radio-button value="online">在线</el-radio-button>
              <el-radio-button value="offline">离线</el-radio-button>
              <el-radio-button value="limited">限速中</el-radio-button>
            </el-radio-group>
          </div>
        </div>
      </template>

      <el-table :data="filteredDevices" row-key="mac" size="small" style="width:100%">
        <el-table-column width="46" align="center">
          <template #header>
            <el-checkbox :model-value="allVisibleSelected"
                         :indeterminate="someVisibleSelected"
                         @change="toggleAllVisible"></el-checkbox>
          </template>
          <template #default="{ row }">
            <el-checkbox :model-value="isSelected(row)"
                         @change="toggleDevice(row, $event)"></el-checkbox>
          </template>
        </el-table-column>
        <el-table-column type="index" label="#" width="56" align="center"></el-table-column>
        <el-table-column label="设备" min-width="220">
          <template #default="{ row }">
            <div class="dev-cell">
              <img class="dev-icon" :src="deviceIconUrl(row.deviceType)"
                   @error="onIconError($event)" alt="">
              <div class="dev-text">
                <div class="dev-line">
                  <span class="dev-name">{{ row.name || row.hostname || '-' }}</span>
                  <span class="fav" :class="{ active: isFavorite(row) }"
                        :title="isFavorite(row) ? '取消收藏' : '收藏'"
                        @click.stop="toggleFavorite(row)">{{ isFavorite(row) ? '★' : '☆' }}</span>
                </div>
                <div class="mono">{{ row.mac }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="IP" width="130" align="center">
          <template #default="{ row }"><span class="mono">{{ row.ip || '-' }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="82" align="center">
          <template #default="{ row }">
            <el-tag :type="row.online ? 'primary' : 'info'" size="small" effect="plain">
              {{ row.online ? '在线' : '离线' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="信号" width="110" align="center">
          <template #default="{ row }">
            <span v-if="row.wired" class="muted">有线</span>
            <span v-else-if="row.rssi == null" class="muted">-</span>
            <span v-else class="signal" :class="'signal-' + signalLevel(row.rssi)"
                  :title="(row.band || '') + ' ' + row.rssi + ' dBm · ' + signalLabel(row.rssi)">
              <i></i><i></i><i></i><i></i>
              <em>{{ row.rssi }} dBm</em>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="下载 (KB/s)" width="120" align="center">
          <template #default="{ row }">{{ formatSpeed(row.downBps) }}</template>
        </el-table-column>
        <el-table-column label="上传 (KB/s)" width="120" align="center">
          <template #default="{ row }">{{ formatSpeed(row.upBps) }}</template>
        </el-table-column>
        <el-table-column label="限速到期" width="160" align="center">
          <template #default="{ row }">
            <span :class="expiryClass(row)">{{ expiryText(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="168" align="center">
          <template #default="{ row }">
            <span class="row-actions">
              <el-dropdown trigger="click" @command="onLimitCommand(row, $event)">
                <el-button type="primary" round>限速</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item :command="10">10 分钟</el-dropdown-item>
                    <el-dropdown-item :command="30">30 分钟</el-dropdown-item>
                    <el-dropdown-item :command="60">1 小时</el-dropdown-item>
                    <el-dropdown-item :command="120">2 小时</el-dropdown-item>
                    <el-dropdown-item :command="240">4 小时</el-dropdown-item>
                    <el-dropdown-item :command="1440">1 天</el-dropdown-item>
                    <el-dropdown-item :command="0" divided>永久</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
              <el-button type="danger" plain round @click="clearLimit(row)">解除</el-button>
            </span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="card">
      <template #header>
        <div class="card-head">
          <div class="head-row">
            <span class="group-label">统计时段</span>
            <el-radio-group v-model="rangeMinutes" @change="onRangeChange">
              <el-radio-button :value="5">5分钟</el-radio-button>
              <el-radio-button :value="30">30分钟</el-radio-button>
              <el-radio-button :value="60">1小时</el-radio-button>
              <el-radio-button :value="360">6小时</el-radio-button>
              <el-radio-button :value="1440">24小时</el-radio-button>
            </el-radio-group>
          </div>
        </div>
      </template>
      <el-row :gutter="16">
        <el-col :xs="24" :md="12">
          <div class="chart-block">
            <div class="chart-title">下载速度 <span class="muted">KB/s</span></div>
            <div ref="downChart" class="chart"></div>
          </div>
        </el-col>
        <el-col :xs="24" :md="12">
          <div class="chart-block">
            <div class="chart-title">上传速度 <span class="muted">KB/s</span></div>
            <div ref="upChart" class="chart"></div>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <el-card shadow="never" class="card">
      <template #header><span class="card-title">系统信息</span></template>
      <div class="sysinfo-grid">
        <div v-for="item in sysinfoItems" :key="item.label" class="sysinfo-item">
          <div class="sysinfo-label">{{ item.label }}</div>
          <div class="sysinfo-value">{{ item.value }}</div>
        </div>
      </div>
    </el-card>
  </div>
</div>

<script src="/__gw/static/vue.global.prod.js"></script>
<script src="/__gw/static/element-plus.full.min.js"></script>
<script src="/__gw/static/echarts.min.js"></script>
<script>
(function () {
  "use strict";

  var COLORS = ["#3a9cf5", "#5ad8a6", "#f6bd16", "#e8684a", "#6dc8ec", "#9270ca", "#ff9d4d", "#269a99"];
  var STORAGE_KEY = "gw_monitor_selection";
  var FILTER_KEY = "gw_monitor_device_filter";
  var RANGE_KEY = "gw_monitor_range_minutes";
  var FAVORITES_KEY = "gw_monitor_favorites";
  var FILTERS = ["all", "favorite", "online", "offline", "limited"];
  var RANGES = [5, 30, 60, 360, 1440];

  function readFilter() {
    try {
      var value = localStorage.getItem(FILTER_KEY);
      return FILTERS.indexOf(value) !== -1 ? value : "all";
    } catch (e) { return "all"; }
  }
  function readRange() {
    try {
      var value = Number(localStorage.getItem(RANGE_KEY));
      return RANGES.indexOf(value) !== -1 ? value : 60;
    } catch (e) { return 60; }
  }
  function ipTail(ip) {
    if (!ip) { return Number.POSITIVE_INFINITY; }
    var parts = String(ip).split(".");
    if (parts.length !== 4) { return Number.POSITIVE_INFINITY; }
    var value = Number(parts[3]);
    return isNaN(value) ? Number.POSITIVE_INFINITY : value;
  }
  function compareByIp(a, b) {
    var tailA = ipTail(a.ip);
    var tailB = ipTail(b.ip);
    if (tailA !== tailB) { return tailA - tailB; }
    var ipA = a.ip || "";
    var ipB = b.ip || "";
    if (ipA !== ipB) { return ipA < ipB ? -1 : 1; }
    return String(a.mac || "").localeCompare(String(b.mac || ""));
  }
  function readFavorites() {
    try {
      var raw = localStorage.getItem(FAVORITES_KEY);
      if (raw === null) { return []; }
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed)
        ? parsed.map(function (m) { return String(m).toLowerCase(); })
        : [];
    } catch (e) { return []; }
  }

  var createApp = Vue.createApp;
  var ElMessage = ElementPlus.ElMessage;

  createApp({
    data: function () {
      return {
        devices: [],
        schedules: [],
        sysinfo: {},
        favorites: readFavorites(),
        selectedMacs: [],
        selectionInitialized: false,
        deviceFilter: readFilter(),
        rangeMinutes: readRange(),
        refreshing: false,
        timer: null
      };
    },
    computed: {
      scheduleByMac: function () {
        var map = {};
        this.schedules.forEach(function (schedule) {
          if (schedule.state !== "pending" && schedule.state !== "active") { return; }
          var existing = map[schedule.mac];
          if (!existing || schedule.id > existing.id) { map[schedule.mac] = schedule; }
        });
        return map;
      },
      filteredDevices: function () {
        var self = this;
        if (this.deviceFilter === "favorite") {
          return this.devices.filter(function (row) { return self.isFavorite(row); });
        }
        if (this.deviceFilter === "online") {
          return this.devices.filter(function (row) { return row.online; });
        }
        if (this.deviceFilter === "offline") {
          return this.devices.filter(function (row) { return !row.online; });
        }
        if (this.deviceFilter === "limited") {
          return this.devices.filter(function (row) {
            return Boolean(self.scheduleByMac[self.macKey(row)]);
          });
        }
        return this.devices;
      },
      allVisibleSelected: function () {
        var self = this;
        return this.filteredDevices.length > 0 && this.filteredDevices.every(function (row) {
          return self.selectedMacs.indexOf(self.macKey(row)) !== -1;
        });
      },
      someVisibleSelected: function () {
        var self = this;
        if (this.allVisibleSelected) { return false; }
        return this.filteredDevices.some(function (row) {
          return self.selectedMacs.indexOf(self.macKey(row)) !== -1;
        });
      },
      onlineDeviceCount: function () {
        return this.devices.filter(function (row) { return row.online; }).length;
      },
      sysinfoItems: function () {
        var info = this.sysinfo || {};
        return [
          {
            label: "运行时间",
            value: info.uptimeSeconds != null ? this.formatDuration(info.uptimeSeconds) : "-"
          },
          { label: "IPv4 地址", value: info.ipv4 || "-" },
          {
            label: "IPv4 在线时长",
            value: info.onlineSeconds != null ? this.formatDuration(info.onlineSeconds) : "-"
          },
          { label: "接入设备", value: this.onlineDeviceCount + " 台" },
          { label: "DNS", value: info.dns || "-" },
          { label: "备用 DNS", value: info.dns2 || "-" }
        ];
      }
    },
    watch: {
      deviceFilter: function (value) {
        try { localStorage.setItem(FILTER_KEY, value); } catch (e) { /* ignore */ }
      },
      rangeMinutes: function (value) {
        try { localStorage.setItem(RANGE_KEY, String(value)); } catch (e) { /* ignore */ }
      }
    },
    mounted: function () {
      var self = this;
      this.initCharts();
      this.refreshAll();
      setTimeout(function () { self.resizeCharts(); }, 200);
      this.timer = setInterval(this.refreshAll, 5000);
      window.addEventListener("resize", this.resizeCharts);
    },
    beforeUnmount: function () {
      clearInterval(this.timer);
      window.removeEventListener("resize", this.resizeCharts);
      if (this.charts) {
        if (this.charts.down) { this.charts.down.dispose(); }
        if (this.charts.up) { this.charts.up.dispose(); }
      }
    },
    methods: {
      macKey: function (row) { return String(row.mac || "").toLowerCase(); },
      // downBps/upBps 是 BytesReceived/BytesSend 差分得到的 B/s；按十进制换成 KB/s
      toKBps: function (value) { return Number(value || 0) / 1000; },
      formatSpeed: function (value) {
        return Math.round(this.toKBps(value)).toLocaleString();
      },
      goHome: function () { location.href = "/"; },
      deviceIconUrl: function (type) {
        var known = {
          phone: 1, pc: 1, notebook: 1, pad: 1, printer: 1, socket: 1,
          fridge: 1, airconditioner: 1, tv: 1, camera: 1, speaker: 1,
          watch: 1, router: 1, ap: 1, nas: 1, washer: 1, other: 1
        };
        var key = String(type || "").toLowerCase();
        return "/__gw/static/devices/" + (known[key] ? key : "default") + ".svg";
      },
      onIconError: function (event) {
        var img = event.target;
        img.onerror = null;
        img.src = "/__gw/static/devices/default.svg";
      },
      signalLevel: function (rssi) {
        var value = Number(rssi);
        if (value >= -50) { return 4; }
        if (value >= -65) { return 3; }
        if (value >= -80) { return 2; }
        return 1;
      },
      signalLabel: function (rssi) {
        return { 4: "信号很好", 3: "信号良好", 2: "信号一般（可用）", 1: "信号较弱" }[
          this.signalLevel(rssi)
        ];
      },

      api: async function (path, options) {
        var response = await fetch(path, Object.assign({ credentials: "same-origin" }, options || {}));
        if (response.status === 401) {
          location.href = "/__gw/login?next=" + encodeURIComponent(location.pathname);
          throw new Error("未登录");
        }
        if (!response.ok) {
          var detail = "";
          try { detail = (await response.json()).detail || ""; } catch (e) { /* ignore */ }
          throw new Error(response.status + (detail ? " " + detail : ""));
        }
        return response.json();
      },

      // ------------------------------------------------------------ selection
      loadSelection: function () {
        try {
          var raw = localStorage.getItem(STORAGE_KEY);
          if (raw === null) { return null; }
          var parsed = JSON.parse(raw);
          return Array.isArray(parsed)
            ? parsed.map(function (m) { return String(m).toLowerCase(); })
            : null;
        } catch (e) { return null; }
      },
      saveSelection: function () {
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(this.selectedMacs)); } catch (e) { /* ignore */ }
      },
      isSelected: function (row) {
        return this.selectedMacs.indexOf(this.macKey(row)) !== -1;
      },
      isFavorite: function (row) {
        return this.favorites.indexOf(this.macKey(row)) !== -1;
      },
      toggleFavorite: function (row) {
        var key = this.macKey(row);
        var index = this.favorites.indexOf(key);
        if (index === -1) { this.favorites.push(key); }
        else { this.favorites.splice(index, 1); }
        try { localStorage.setItem(FAVORITES_KEY, JSON.stringify(this.favorites)); } catch (e) { /* ignore */ }
      },
      toggleDevice: function (row, checked) {
        var key = this.macKey(row);
        var index = this.selectedMacs.indexOf(key);
        if (checked && index === -1) { this.selectedMacs.push(key); }
        if (!checked && index !== -1) { this.selectedMacs.splice(index, 1); }
        this.saveSelection();
        this.refreshSeries().catch(function (e) { console.error(e); });
      },
      toggleAllVisible: function (checked) {
        var self = this;
        var wanted = this.filteredDevices.map(this.macKey);
        if (checked) {
          wanted.forEach(function (key) {
            if (self.selectedMacs.indexOf(key) === -1) { self.selectedMacs.push(key); }
          });
        } else {
          this.selectedMacs = this.selectedMacs.filter(function (key) {
            return wanted.indexOf(key) === -1;
          });
        }
        this.saveSelection();
        this.refreshSeries().catch(function (e) { console.error(e); });
      },

      // ---------------------------------------------------------------- data
      refreshDevices: async function () {
        var data = await this.api("/api/devices");
        // 按 IPv4 末段数字升序，没有 IP 的设备排最后
        this.devices = (data.devices || []).slice().sort(compareByIp);

        if (!this.selectionInitialized) {
          var stored = this.loadSelection();
          if (stored !== null) {
            this.selectedMacs = stored;
          } else {
            this.selectedMacs = this.devices
              .filter(function (row) { return row.online; })
              .sort(function (a, b) { return (b.downBps + b.upBps) - (a.downBps + a.upBps); })
              .slice(0, 3)
              .map(this.macKey);
          }
          this.selectionInitialized = true;
          this.saveSelection();
          this.refreshSeries().catch(function (e) { console.error(e); });
        }
      },
      refreshSchedules: async function () {
        var data = await this.api("/api/qos/schedules");
        this.schedules = data.schedules || [];
      },
      refreshSysinfo: async function () {
        try {
          this.sysinfo = await this.api("/api/sysinfo");
        } catch (e) {
          console.error(e);
        }
      },
      formatDuration: function (seconds) {
        var total = Math.max(0, Math.floor(Number(seconds || 0)));
        var days = Math.floor(total / 86400);
        total %= 86400;
        var hours = Math.floor(total / 3600);
        total %= 3600;
        var minutes = Math.floor(total / 60);
        var secs = total % 60;
        var parts = [];
        if (days > 0) { parts.push(days + " 天"); }
        if (hours > 0 || days > 0) { parts.push(hours + " 小时"); }
        parts.push(minutes + " 分");
        parts.push(secs + " 秒");
        return parts.join(" ");
      },
      refreshSeries: async function () {
        if (!this.charts || !this.charts.down) { return; }
        if (!this.selectedMacs.length) { this.updateSeries([]); return; }
        var to = Date.now();
        var from = to - this.rangeMinutes * 60000;
        var params = new URLSearchParams({
          macs: this.selectedMacs.join(","),
          from: String(from),
          to: String(to)
        });
        var data = await this.api("/api/monitor/series?" + params.toString());
        this.updateSeries(data.series || []);
      },
      refreshAll: async function () {
        if (this.refreshing) { return; }
        this.refreshing = true;
        try {
          await this.refreshDevices();
          await this.refreshSeries();
          await this.refreshSchedules();
          await this.refreshSysinfo();
        } catch (e) { console.error(e); }
        this.refreshing = false;
      },

      // --------------------------------------------------------------- chart
      initCharts: function () {
        if (!this.$refs.downChart || !this.$refs.upChart) { return; }
        this.charts = {};
        this.charts.down = echarts.init(this.$refs.downChart);
        this.charts.up = echarts.init(this.$refs.upChart);
        this.charts.down.group = "monitor";
        this.charts.up.group = "monitor";
        echarts.connect("monitor");
        this.charts.down.setOption(this.baseOption());
        this.charts.up.setOption(this.baseOption());
        this.resizeCharts();
      },
      resizeCharts: function () {
        if (this.charts) {
          if (this.charts.down) { this.charts.down.resize(); }
          if (this.charts.up) { this.charts.up.resize(); }
        }
      },
      baseOption: function () {
        var self = this;
        return {
          color: COLORS,
          grid: { left: 72, right: 22, top: 44, bottom: 54 },
          tooltip: {
            trigger: "axis",
            valueFormatter: function (value) {
              return value == null ? "-" : Math.round(Number(value)).toLocaleString() + " KB/s";
            }
          },
          legend: { type: "scroll", top: 0, textStyle: { color: "#5a6472" } },
          xAxis: {
            type: "time",
            boundaryGap: false,
            splitNumber: 6,
            axisLabel: {
              color: "#8a94a6",
              hideOverlap: true,
              formatter: function (value) {
                var date = new Date(value);
                var hh = String(date.getHours()).padStart(2, "0");
                var mm = String(date.getMinutes()).padStart(2, "0");
                if (self.rangeMinutes >= 1440) {
                  return (date.getMonth() + 1) + "/" + date.getDate() + " " + hh + ":" + mm;
                }
                return hh + ":" + mm;
              }
            },
            axisLine: { lineStyle: { color: "#dcdfe6" } }
          },
          yAxis: {
            type: "value", min: 0,
            axisLabel: { color: "#8a94a6" },
            splitLine: { lineStyle: { color: "#eef1f5" } }
          },
          dataZoom: [{ type: "inside" }, { type: "slider", height: 16, bottom: 12 }],
          series: []
        };
      },
      buildSeries: function (items, metric) {
        var self = this;
        return items.map(function (item) {
          return {
            id: item.mac,
            name: item.name || item.mac,
            type: "line",
            showSymbol: false,
            connectNulls: false,
            sampling: "lttb",
            lineStyle: { width: 2 },
            data: (item[metric] || []).map(function (point) {
              return [point[0], self.toKBps(point[1])];
            })
          };
        });
      },
      updateSeries: function (items) {
        if (!this.charts || !this.charts.down) { return; }
        var names = items.map(function (item) { return item.name || item.mac; });
        this.charts.down.setOption(
          { legend: { data: names }, series: this.buildSeries(items, "down") },
          { replaceMerge: ["series"] }
        );
        this.charts.up.setOption(
          { legend: { data: names }, series: this.buildSeries(items, "up") },
          { replaceMerge: ["series"] }
        );
      },
      resetZoom: function () {
        var action = { type: "dataZoom", start: 0, end: 100 };
        this.charts.down.dispatchAction(action);
        this.charts.up.dispatchAction(action);
      },
      onRangeChange: function () {
        this.resetZoom();
        this.refreshSeries().catch(function (e) { console.error(e); });
      },

      // -------------------------------------------------------------- limits
      expiryText: function (row) {
        var schedule = this.scheduleByMac[this.macKey(row)];
        if (!schedule) { return "-"; }
        if (schedule.state === "pending") { return "即将生效"; }
        if (!schedule.endsAt) { return "永久"; }
        var left = schedule.endsAt - Date.now();
        if (left <= 0) { return "已到期"; }
        return this.formatRemaining(left) + " 后到期";
      },
      expiryClass: function (row) {
        var schedule = this.scheduleByMac[this.macKey(row)];
        if (!schedule) { return "muted"; }
        if (schedule.state === "pending") { return "muted"; }
        if (!schedule.endsAt) { return "permanent"; }
        if (schedule.endsAt - Date.now() < 600000) { return "expiring"; }
        return "";
      },
      formatRemaining: function (ms) {
        var totalMinutes = Math.max(1, Math.ceil(ms / 60000));
        var hours = Math.floor(totalMinutes / 60);
        var minutes = totalMinutes % 60;
        if (hours > 0) {
          return hours + " 小时 " + minutes + " 分";
        }
        return minutes + " 分";
      },
      onLimitCommand: function (row, minutes) {
        this.applyLimit(row, Number(minutes));
      },
      applyLimit: async function (row, minutes) {
        var key = this.macKey(row);
        // 上下行固定 0.01 Mbps = 10 Kbps
        var body = { mac: key, downKbps: 10, upKbps: 10 };
        if (minutes > 0) {
          body.durationMinutes = minutes;
        } else {
          body.permanent = true;
        }
        try {
          await this.api("/api/qos/schedules", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
          });
          var suffix = minutes > 0 ? "，" + minutes + " 分钟后自动解除" : "（永久）";
          ElMessage.success((row.name || key) + " 已限速 0.01/0.01 Mbps" + suffix);
          await this.refreshSchedules();
        } catch (e) {
          ElMessage.error("限速失败：" + e.message);
        }
      },
      clearLimit: async function (row) {
        var key = this.macKey(row);
        try {
          await this.refreshSchedules();
          var schedules = this.schedules.filter(function (schedule) {
            return schedule.mac === key
              && (schedule.state === "pending" || schedule.state === "active");
          });
          for (var i = 0; i < schedules.length; i += 1) {
            await this.api("/api/qos/schedules/" + schedules[i].id, { method: "DELETE" });
          }
          await this.api("/api/devices/" + encodeURIComponent(key) + "/qos", { method: "DELETE" });
          ElMessage.success((row.name || key) + " 已解除限速");
          await this.refreshSchedules();
        } catch (e) {
          ElMessage.error("解除失败：" + e.message);
        }
      }
    }
  }).use(ElementPlus).mount("#app");
})();
</script>
</body>
</html>"""
