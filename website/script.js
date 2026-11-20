/* LCL 官网：从 GitHub Releases 获取最新版本号与下载地址。
 * 接口不可用时保留 HTML 中的静态版本与 releases/latest 链接。 */
(function () {
  "use strict";

  var REPO = "wengdows/Lightning-Craft-Launcher";
  var LATEST_URL = "https://github.com/" + REPO + "/releases/latest";
  var FALLBACK_VERSION = "v1.0.1";

  document.getElementById("year").textContent = String(new Date().getFullYear());

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function setHref(id, href) {
    var el = document.getElementById(id);
    if (el) el.href = href;
  }

  function applyRelease(rel) {
    var tag = rel.tag_name || FALLBACK_VERSION;
    if (tag.charAt(0) !== "v") tag = "v" + tag;

    var exe = (rel.assets || []).filter(function (a) {
      return /\.exe$/i.test(a.name || "");
    })[0];
    var url = exe ? exe.browser_download_url : LATEST_URL;

    setText("dl-hero-text", "下载最新版 " + tag);
    setText("dl-main-text", "下载最新版 " + tag);
    setText("dl-version", tag);
    setHref("dl-hero", url);
    setHref("dl-main", url);

    if (rel.published_at) {
      var d = new Date(rel.published_at);
      if (!isNaN(d.getTime())) {
        setText("dl-date", "发布于 " + d.getFullYear() + "-" +
          String(d.getMonth() + 1).padStart(2, "0") + "-" +
          String(d.getDate()).padStart(2, "0") + " · GitHub Releases");
      }
    }
    if (exe && exe.size) {
      var mb = (exe.size / 1024 / 1024).toFixed(1);
      setText("dl-main-text", "下载最新版 " + tag + "（" + mb + " MB）");
    }
  }

  fetch("https://api.github.com/repos/" + REPO + "/releases/latest", {
    headers: { "Accept": "application/vnd.github+json" }
  })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(applyRelease)
    .catch(function () {
      /* 网络失败或 API 限流：保持静态版本号，按钮仍指向 releases/latest */
    });
})();
