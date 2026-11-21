/* qwensese · 附加交互（独立于 app.js，避免动那个 82KB 的大文件）
 * 1) 首次访问的 18+ 年龄确认（前端软门槛；服务端无法真正验证年龄）
 * 2) 「硬件选型」按钮：调用 /api/hardware，展示显卡/显存与推荐模型、下载导入命令
 */
(function () {
  'use strict';

  function qs(sel, root) { return (root || document).querySelector(sel); }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  async function getState() {
    try {
      var r = await fetch('/api/state');
      if (!r.ok) return {};
      return await r.json();
    } catch (e) {
      return {};
    }
  }

  /* ------------------------------------------------ 18+ 确认 */
  async function ageGate() {
    var st = await getState();
    if (st.adult_only === false) return;
    var ack = null;
    try { ack = localStorage.getItem('qw_adult_ack'); } catch (e) {}
    if (ack === '1') return;

    var box = document.createElement('div');
    box.className = 'qw-gate';
    box.innerHTML =
      '<div class="qw-gate-card">' +
        '<h2>内容提示 · 18+</h2>' +
        '<p>本项目面向<b>成年人</b>，用于虚构角色的本机角色扮演。默认示例角色均为 18 岁以上虚构人物；' +
        '尺度档位包含成人向描写，推荐底座是权重层面消融了拒答方向的衍生模型，<b>没有内容审查兜底</b>。</p>' +
        '<p>请确认：你已满 18 岁（或所在司法辖区的成年年龄）；你不会用它生成涉及未成年人、真实人物或违法内容；' +
        '你自行承担所在地区法律与平台规则下的使用责任。</p>' +
        '<p class="qw-dim">这是一个前端提示，服务端无法验证年龄；把服务开放到局域网/公网前请设置 access_key。</p>' +
        '<div class="qw-gate-actions">' +
          '<button class="btn" id="qw-adult-yes">我已满 18 岁，继续</button>' +
          '<button class="btn" id="qw-adult-no">离开</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(box);

    qs('#qw-adult-yes', box).onclick = function () {
      try { localStorage.setItem('qw_adult_ack', '1'); } catch (e) {}
      box.remove();
    };
    qs('#qw-adult-no', box).onclick = function () {
      box.remove();
      document.body.innerHTML =
        '<div style="padding:48px 24px;text-align:center;color:#9a9aa6;line-height:1.8">' +
        '已退出。本项目面向成年人使用，未满 18 岁请勿继续。</div>';
    };
  }

  /* ------------------------------------------------ 硬件选型 */
  function showPanel(data) {
    var old = qs('#hw-panel');
    if (old) old.remove();
    var p = document.createElement('div');
    p.id = 'hw-panel';
    p.className = 'qw-hw-panel';

    if (!data || !data.ok) {
      p.innerHTML = '<h3>硬件检测失败</h3><p>' + esc((data && data.error) || '未知错误') +
        '</p><p class="qw-dim">可以在项目根目录手动运行：python scripts/model_advisor.py</p>' +
        '<button class="btn" id="hw-close">关闭</button>';
      document.body.appendChild(p);
      qs('#hw-close', p).onclick = function () { p.remove(); };
      return;
    }

    var hw = data.hardware || {};
    var rec = data.recommendation || {};
    var pr = rec.primary || {};
    var gpuName = (hw.primary_gpu || {}).name || '未检测到 GPU';
    var warnings = (rec.warnings || []).map(function (w) {
      return '<p class="qw-warn">注意：' + esc(w) + '</p>';
    }).join('');

    p.innerHTML =
      '<h3>硬件检测与模型推荐 <span class="qw-dim">v' + esc((rec.version || '')) + '</span></h3>' +
      '<div class="qw-hw-grid">' +
        '<div>显卡</div><div>' + esc(gpuName) + '（显存 ' + esc(hw.vram_gb || 0) + ' GB）</div>' +
        '<div>CPU / 内存</div><div>' + esc(hw.cpu || '') + ' · ' + esc(hw.ram_gb || 0) + ' GB</div>' +
        '<div>档位</div><div>' + esc(rec.class_label || '') + '</div>' +
        '<div>推荐模型</div><div><b>' + esc(pr.label || '') + ' · ' + esc(pr.quant || '') + '</b><br>' +
          '约 ' + esc(pr.size_gb || 0) + ' GB 文件，最低 ' + esc(pr.min_vram_gb || 0) + ' GB 显存<br>' +
          '预计速度 ' + esc(pr.speed_hint || '') + '</div>' +
        '<div>建议上下文</div><div>num_ctx = ' + esc(rec.suggested_num_ctx || '') + '</div>' +
      '</div>' + warnings +
      '<p class="qw-dim">在项目根目录执行（会自动断点续传 + SHA256 校验）：</p>' +
      '<pre class="qw-cmd">' + esc(rec.download_command || '') + '</pre>' +
      '<p class="qw-dim">下载完成后导入 Ollama：</p>' +
      '<pre class="qw-cmd">' + esc(rec.import_command || '') + '</pre>' +
      '<p class="qw-dim">完整选型表与说明见仓库里的 docs/model-selection.md；' +
      'Windows 也可以直接用 scripts/model-advisor.ps1。</p>' +
      '<button class="btn" id="hw-close">关闭</button>';

    document.body.appendChild(p);
    qs('#hw-close', p).onclick = function () { p.remove(); };
  }

  var hwBtn = qs('#hw-btn');
  if (hwBtn) {
    hwBtn.addEventListener('click', async function () {
      hwBtn.disabled = true;
      var label = hwBtn.textContent;
      hwBtn.textContent = '⏳ 检测中…';
      var data = null;
      try {
        var r = await fetch('/api/hardware');
        data = await r.json();
      } catch (e) {
        data = { ok: false, error: String(e) };
      }
      hwBtn.disabled = false;
      hwBtn.textContent = label;
      showPanel(data);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ageGate);
  } else {
    ageGate();
  }
})();
