/**
 * Virtual AI Companion — Live2D Controller (Frontend)
 *
 * Uses PIXI.js + pixi-live2d-display to render and control the Live2D model.
 * Communicates with Python backend via QWebChannel.
 */

// --- State ---
let app = null;
let model = null;
let bridge = null;
let modelLoaded = false;
let isSpeaking = false;
let lastMouthValue = 0;
let lastExpression = null;
let idleTimer = null;
let idleMotionInterval = 15000;
let responseMode = 'voice'; // 'voice' or 'text'
let windowFocused = false;
let modelSelected = false;
let windowSelected = false;
let selectionIndicator = null;

// Parameter IDs (standard Cubism parameter names)
const PARAM_MOUTH_OPEN_Y = "ParamMouthOpenY";
const PARAM_EYE_OPEN_L = "ParamEyeLOpen";
const PARAM_EYE_OPEN_R = "ParamEyeROpen";
const PARAM_ANGLE_X = "ParamAngleX";
const PARAM_ANGLE_Y = "ParamAngleY";
const PARAM_BODY_ANGLE_X = "ParamBodyAngleX";

// ==== Expression Mapping ====
// Each emotion → list of candidates; first match wins.
// Supports: English names (Haru sample) + 中文名 (芙宁娜/Furina)
const EXPRESSION_CANDIDATES = {
    "happy":     ["Happy", "星星", "猫猫嘴", "小脸红"],
    "sad":       ["Sad", "哭"],
    "angry":     ["Angry", "生气"],
    "surprised": ["Surprise", "汗", "大聪明"],
    "neutral":   ["Neutral"],
};

// Extra expressions cycled randomly during idle (purely cosmetic)
// All 17 Furina expressions are used: core emotions above + these idle ones
const IDLE_EXPRESSIONS = [
    "呆毛电风扇", "喝饮料", "帽子", "托脸",
    "拿勺子", "拿蛋糕", "捂嘴", "牌子", "走路切换", "鱼鱼",
    // Also re-use some core ones during idle for variety
    "星星", "猫猫嘴", "汗", "大聪明", "小脸红",
];

// ==== Motion Mapping ====
// Regular idle motions (play frequently)
const IDLE_MOTIONS = [
    "待机动画", "摊手动画", "Idle", "Idle_01", "Idle_02",
    "TouchHead", "TouchBody",
];

// 变身 (transformation) motions — play rarely as special animations
const TRANSFORM_MOTIONS = ["变芒", "变荒"];

// --- Initialize PIXI Application ---
async function initApp(canvasId, modelUrl) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) {
        updateStatus("Canvas not found");
        return false;
    }

    // Resize canvas to match window size (does NOT reposition model)
    function resize() {
        canvas.width = window.innerWidth * window.devicePixelRatio;
        canvas.height = window.innerHeight * window.devicePixelRatio;
        canvas.style.width = window.innerWidth + 'px';
        canvas.style.height = window.innerHeight + 'px';
        if (app && app.renderer) {
            app.renderer.resize(canvas.width, canvas.height);
        }
    }
    window.addEventListener('resize', resize);
    resize();

    // Center model in window and auto-scale to fit
    function centerAndScaleModel() {
        if (!model || !modelLoaded) return;
        model.x = canvas.width / 2;
        model.y = canvas.height * 0.55;
        const s = Math.min(
            canvas.width * 0.35 / model.internalModel.width,
            canvas.height * 0.6 / model.internalModel.height
        );
        model.scale.set(s);
    }

    // Create PIXI Application
    try {
        app = new PIXI.Application({
            view: canvas,
            width: canvas.width,
            height: canvas.height,
            transparent: true,
            backgroundAlpha: 0,
            antialias: true,
            resolution: window.devicePixelRatio || 1,
            autoDensity: true,
        });
        updateStatus("PIXI initialized");
    } catch (e) {
        updateStatus("PIXI init failed: " + e.message);
        return false;
    }

    // Load Live2D model
    try {
        updateStatus("Loading model...");
        console.log('[Model] Fetching from URL:', modelUrl);
        model = await PIXI.live2d.Live2DModel.from(modelUrl);
        console.log('[Model] Loaded successfully. Dimensions:', model.width, 'x', model.height);
        console.log('[Model] Original model size:', model.internalModel.width, 'x', model.internalModel.height);

        modelLoaded = true;

        // Auto-center and scale model to fit window
        centerAndScaleModel();
        console.log('[Model] Canvas:', canvas.width, 'x', canvas.height,
                    'Pos:', model.x, model.y, 'Scale:', model.scale.x);

        model.interactive = true;
        app.stage.addChild(model);
        console.log('[Model] Added to stage. Stage children:', app.stage.children.length);

        // ---- Drag & Drop support ----
        setupInteraction(canvas);

        updateStatus("Model loaded ✓");
        startIdleMotions();
        return true;

    } catch (e) {
        console.error('[Model] Load failed:', e.message, e.stack);
        updateStatus("Model load failed: " + e.message);
        return false;
    }
}

// --- Model Selection Indicator ---
function showModelIndicator() {
    if (!model || !app) return;
    if (!selectionIndicator) {
        selectionIndicator = new PIXI.Graphics();
        app.stage.addChild(selectionIndicator);
    }
    const w = model.internalModel.width * model.scale.x;
    const h = model.internalModel.height * model.scale.y;
    selectionIndicator.clear();
    selectionIndicator.lineStyle(2, 0x64FFB4, 0.85);
    selectionIndicator.drawRoundedRect(-w / 2 - 6, -h / 2 - 6, w + 12, h + 12, 10);
    selectionIndicator.x = model.x;
    selectionIndicator.y = model.y;
}

function hideModelIndicator() {
    if (selectionIndicator) selectionIndicator.clear();
}

function updateModelIndicator() {
    if (modelSelected) showModelIndicator();
}

function selectModel() {
    modelSelected = true;
    windowSelected = false;
    showModelIndicator();
    document.body.classList.remove('window-focused');
    document.body.classList.add('model-selected');
}

function selectWindow() {
    modelSelected = false;
    windowSelected = true;
    hideModelIndicator();
    document.body.classList.remove('model-selected');
    document.body.classList.add('window-focused');
}

function deselectAll() {
    modelSelected = false;
    windowSelected = false;
    hideModelIndicator();
    document.body.classList.remove('window-focused', 'model-selected');
    dragMode = null;
}

// --- Drag / Scale / Interaction ---
let dragMode = null; // 'model' | 'window' | null
let dragStart = { x: 0, y: 0 };

function setupInteraction(canvas) {
    // ---- Model click → select model ----
    if (model) {
        model.on('pointerdown', (e) => {
            if (modelSelected) {
                // Start model drag (use DOM coords to match pointermove)
                dragMode = 'model';
                const ev = e.data.originalEvent;
                dragStart = { x: ev.clientX, y: ev.clientY };
            } else {
                selectModel();
            }
        });

        // Eye tracking
        model.on('pointermove', (e) => {
            const pt = e.data.getLocalPosition(model);
            const rx = (pt.x / model.width - 0.5) * 2 * 15;
            const ry = (pt.y / model.height - 0.5) * 2 * 15;
            setParam(PARAM_ANGLE_X, rx);
            setParam(PARAM_ANGLE_Y, ry);
            setParam(PARAM_BODY_ANGLE_X, rx * 0.3);
        });
    }

    // ---- Canvas click (non-model area) → select window ----
    canvas.addEventListener('pointerdown', (e) => {
        // Check if click falls within model bounds
        if (model && modelLoaded) {
            const rect = canvas.getBoundingClientRect();
            const cx = (e.clientX - rect.left) * (canvas.width / rect.width);
            const cy = (e.clientY - rect.top) * (canvas.height / rect.height);
            const mw = model.internalModel.width * model.scale.x;
            const mh = model.internalModel.height * model.scale.y;
            const mx = model.x - mw / 2;
            const my = model.y - mh / 2;
            if (cx >= mx && cx <= mx + mw && cy >= my && cy <= my + mh) {
                return; // Click is on the model — PIXI handles it
            }
        }
        if (windowSelected) {
            dragMode = 'window';
            dragStart = { x: e.clientX, y: e.clientY };
            canvas.setPointerCapture(e.pointerId);
        } else {
            selectWindow();
        }
    });

    // ---- Scroll: model scale or window resize ----
    canvas.addEventListener('wheel', (e) => {
        e.preventDefault();
        if (modelSelected) {
            const s = Math.max(0.02, Math.min(0.5, model.scale.x + (e.deltaY > 0 ? -0.04 : 0.04)));
            model.scale.set(s);
            updateModelIndicator();
        } else if (windowSelected) {
            const delta = e.deltaY > 0 ? -30 : 30;
            if (bridge && bridge.resize_window) bridge.resize_window(delta);
        }
    }, { passive: false });

    // ---- Drag: model move or window move ----
    window.addEventListener('pointermove', (e) => {
        if (dragMode === 'model') {
            const dx = e.clientX - dragStart.x;
            const dy = e.clientY - dragStart.y;
            dragStart = { x: e.clientX, y: e.clientY };
            model.x += dx;
            model.y += dy;
            updateModelIndicator();
        } else if (dragMode === 'window') {
            const dx = Math.round(e.clientX - dragStart.x);
            const dy = Math.round(e.clientY - dragStart.y);
            dragStart = { x: e.clientX, y: e.clientY };
            if (bridge && bridge.move_window) bridge.move_window(dx, dy);
        }
    });

    window.addEventListener('pointerup', () => {
        if (dragMode === 'model') dragMode = null;
        // window drag releases on pointerup too
        if (dragMode === 'window') dragMode = null;
    });

    // ---- Keyboard ----
    window.addEventListener('keydown', (e) => {
        if (!model || !modelLoaded) return;
        if (document.activeElement && document.activeElement.tagName === 'INPUT') return;

        const modelStep = 10;
        const winStep = 20;

        switch (e.key) {
            case 'ArrowUp':
                if (modelSelected) { model.y -= modelStep; updateModelIndicator(); }
                else if (windowSelected && bridge) bridge.move_window(0, -winStep);
                e.preventDefault(); break;
            case 'ArrowDown':
                if (modelSelected) { model.y += modelStep; updateModelIndicator(); }
                else if (windowSelected && bridge) bridge.move_window(0, winStep);
                e.preventDefault(); break;
            case 'ArrowLeft':
                if (modelSelected) { model.x -= modelStep; updateModelIndicator(); }
                else if (windowSelected && bridge) bridge.move_window(-winStep, 0);
                e.preventDefault(); break;
            case 'ArrowRight':
                if (modelSelected) { model.x += modelStep; updateModelIndicator(); }
                else if (windowSelected && bridge) bridge.move_window(winStep, 0);
                e.preventDefault(); break;
            case '+': case '=':
                if (modelSelected) {
                    const s = Math.min(0.5, model.scale.x + 0.04);
                    model.scale.set(s);
                    updateModelIndicator();
                } else if (windowSelected && bridge) bridge.resize_window(40);
                e.preventDefault(); break;
            case '-':
                if (modelSelected) {
                    const s = Math.max(0.02, model.scale.x - 0.04);
                    model.scale.set(s);
                    updateModelIndicator();
                } else if (windowSelected && bridge) bridge.resize_window(-40);
                e.preventDefault(); break;
            case '0':
                if (modelSelected) {
                    // Reset model to center
                    model.x = canvas.width / 2;
                    model.y = canvas.height * 0.55;
                    const s = Math.min(
                        canvas.width * 0.35 / model.internalModel.width,
                        canvas.height * 0.6 / model.internalModel.height
                    );
                    model.scale.set(s);
                    updateModelIndicator();
                } else if (windowSelected && bridge) bridge.reset_window();
                e.preventDefault(); break;
            case 'Escape':
                deselectAll();
                e.preventDefault(); break;
        }
    });
}

// --- Parameter Control ---
function setParam(paramId, value) {
    if (!model || !modelLoaded) return;
    try {
        model.internalModel.coreModel.setParameterValueById(paramId, value);
    } catch (e) {
        // Parameter may not exist on this model — ignore silently
    }
}

function getParam(paramId) {
    if (!model || !modelLoaded) return 0;
    try {
        return model.internalModel.coreModel.getParameterValueById(paramId);
    } catch (e) {
        return 0;
    }
}

// --- Expression Control ---
function setExpression(name) {
    if (!model || !modelLoaded) return;
    const candidates = EXPRESSION_CANDIDATES[name] || [name];
    for (const exprName of candidates) {
        try {
            model.expression(exprName);
            return;
        } catch (e) {
            continue; // try next candidate
        }
    }
}

// --- Mouth (Lip-sync) ---
function setMouthOpen(value) {
    if (!model || !modelLoaded) return;
    // Smooth transition for natural movement
    const smoothed = lastMouthValue * 0.6 + value * 0.4;
    lastMouthValue = smoothed;
    setParam(PARAM_MOUTH_OPEN_Y, Math.min(1.0, Math.max(0.0, smoothed)));
}

// --- Speaking Animation ---
function startSpeaking() {
    isSpeaking = true;
    tryMotion("Talk");
}

function stopSpeaking() {
    isSpeaking = false;
    lastMouthValue = 0;
    setParam(PARAM_MOUTH_OPEN_Y, 0);
}

// --- Motion ---
function tryMotion(name) {
    if (!model || !modelLoaded) return false;
    try {
        model.motion(name);
        return true;
    } catch (e) {
        return false;
    }
}

// Try each name in the list; return the one that succeeded (or null)
function tryMotionList(names) {
    for (const name of names) {
        if (tryMotion(name)) return name;
    }
    return null;
}

// Try setting an expression by name; return whether it succeeded
function tryExpression(name) {
    if (!model || !modelLoaded) return false;
    try {
        model.expression(name);
        return true;
    } catch (e) {
        return false;
    }
}

function playRandomIdle() {
    if (isSpeaking) return;
    if (!model || !modelLoaded) return;

    const roll = Math.random();

    // 10% chance: transformation (变芒/变荒)
    if (roll < 0.10) {
        const m = tryMotionList(shuffle([...TRANSFORM_MOTIONS]));
        if (m) return;
    }

    // 40% chance: idle motion (待机/摊手)
    if (roll < 0.50) {
        const m = tryMotionList(shuffle([...IDLE_MOTIONS]));
        if (m) return;
    }

    // 50% chance: idle expression (random fun expression)
    // Pick one that is different from the last
    const pool = IDLE_EXPRESSIONS.filter(e => e !== lastExpression);
    const candidates = pool.length > 0 ? shuffle(pool) : shuffle([...IDLE_EXPRESSIONS]);
    for (const expr of candidates) {
        if (tryExpression(expr)) {
            lastExpression = expr;
            return;
        }
    }
}

// Fisher-Yates shuffle (returns new array)
function shuffle(arr) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
}

function startIdleMotions() {
    stopIdleMotions();
    // Random interval between 10 and 25 seconds
    function scheduleNext() {
        const delay = idleMotionInterval * (0.7 + Math.random() * 0.6);
        idleTimer = setTimeout(() => {
            playRandomIdle();
            scheduleNext();
        }, delay);
    }
    scheduleNext();
}

function stopIdleMotions() {
    if (idleTimer) {
        clearTimeout(idleTimer);
        idleTimer = null;
    }
}

// --- Eye Blinking (natural blink cycle) ---
let blinkTimer = null;
function startBlinking() {
    function blink() {
        if (!model || !modelLoaded) return;
        // Quick blink: close then open
        const duration = 150; // ms
        const closeTime = 50;
        const openTime = duration - closeTime;

        setParam(PARAM_EYE_OPEN_L, 0);
        setParam(PARAM_EYE_OPEN_R, 0);
        setTimeout(() => {
            setParam(PARAM_EYE_OPEN_L, 1);
            setParam(PARAM_EYE_OPEN_R, 1);
        }, closeTime);

        // Next blink in 2-6 seconds
        blinkTimer = setTimeout(blink, 2000 + Math.random() * 4000);
    }
    // Start first blink after a short delay
    blinkTimer = setTimeout(blink, 1000 + Math.random() * 2000);
}

function stopBlinking() {
    if (blinkTimer) {
        clearTimeout(blinkTimer);
        blinkTimer = null;
    }
}

// --- Status Display ---
function updateStatus(msg) {
    const el = document.getElementById('status');
    if (el) {
        el.style.display = 'block';
        el.textContent = msg;
    }
}

function hideStatus() {
    const el = document.getElementById('status');
    if (el) el.style.display = 'none';
}

// --- Focus Border (visible when window is selected) ---
function setWindowFocused(focused) {
    windowFocused = focused;
    if (focused) {
        // Window gained focus via clicking model area → auto-select window
        if (!modelSelected && !windowSelected) {
            selectWindow();
        }
    } else {
        // Window lost focus → deselect everything
        deselectAll();
    }
}

// --- Toast (floating AI response near model) ---
let toastTimer = null;

function showToast(text) {
    const el = document.getElementById('toast');
    if (!el) return;
    // Clear any pending hide
    if (toastTimer) { clearTimeout(toastTimer); toastTimer = null; }
    el.textContent = text;
    el.classList.add('show');
    // Auto-hide after 8 seconds
    toastTimer = setTimeout(() => {
        el.classList.remove('show');
        toastTimer = null;
    }, 8000);
}

function hideToast() {
    const el = document.getElementById('toast');
    if (!el) return;
    if (toastTimer) { clearTimeout(toastTimer); toastTimer = null; }
    el.classList.remove('show');
}

// --- Chat History ---
function addChatBubble(text, role, expression) {
    const history = document.getElementById('chat-history');
    if (!history) return;
    const div = document.createElement('div');
    div.className = 'chat-bubble ' + role;
    div.textContent = text;
    history.appendChild(div);
    history.classList.add('has-messages');
    history.scrollTop = history.scrollHeight;
}

function setResponseMode(mode) {
    responseMode = mode;
    const btn = document.getElementById('mode-toggle');
    if (btn) {
        if (mode === 'text') {
            btn.textContent = '📝';
            btn.classList.add('text-mode');
            btn.title = '当前：纯文字模式（点击切换语音）';
        } else {
            btn.textContent = '🔊';
            btn.classList.remove('text-mode');
            btn.title = '当前：语音模式（点击切换纯文字）';
        }
    }
}

function toggleResponseMode() {
    const newMode = responseMode === 'voice' ? 'text' : 'voice';
    setResponseMode(newMode);
    // Notify Python
    if (bridge && bridge.on_mode_toggle) {
        bridge.on_mode_toggle(newMode);
    }
}

// --- QWebChannel Bridge Setup ---
function setupBridge() {
    if (typeof QWebChannel === 'undefined') {
        updateStatus("QWebChannel not available - running without Python bridge");
        return;
    }

    try {
        new QWebChannel(qt.webChannelTransport, function (channel) {
            bridge = channel.objects.bridge;
            if (!bridge) {
                updateStatus("Bridge object not found");
                return;
            }

            // Wire up signals from Python
            bridge.set_expression_signal.connect(function (name) {
                setExpression(name);
            });

            bridge.set_mouth_open_signal.connect(function (value) {
                setMouthOpen(value);
            });

            bridge.start_speaking_signal.connect(function () {
                startSpeaking();
            });

            bridge.stop_speaking_signal.connect(function () {
                stopSpeaking();
            });

            bridge.play_random_idle_signal.connect(function () {
                playRandomIdle();
            });

            bridge.set_param_signal.connect(function (paramId, value) {
                setParam(paramId, value);
            });

            bridge.set_idle_interval_signal.connect(function (intervalMs) {
                idleMotionInterval = intervalMs;
                startIdleMotions();
            });

            bridge.show_response_signal.connect(function (text, expression) {
                addChatBubble(text, 'ai', expression);
                showToast(text);
            });

            bridge.show_user_message_signal.connect(function (text) {
                addChatBubble(text, 'user');
            });

            bridge.window_focus_changed_signal.connect(function (focused) {
                setWindowFocused(focused);
            });

            // Notify Python that frontend is ready
            if (bridge.on_frontend_ready) {
                bridge.on_frontend_ready(true);
            }

            hideStatus();  // All ready, hide loading indicator
        });
    } catch (e) {
        updateStatus("Bridge setup failed: " + e.message);
    }
}

// --- Main Initialization ---
window.addEventListener('DOMContentLoaded', async () => {
    setupBridge();

    // Read model path from URL parameter (passed by Python)
    const params = new URLSearchParams(window.location.search);
    const modelParam = params.get('model');
    // Resolve to absolute URL (path may be relative or absolute on the server)
    const modelPath = modelParam
        ? new URL(modelParam, window.location.href).href
        : new URL('../models/Haru/Haru.model3.json', window.location.href).href;

    console.log('[Model] Loading:', modelPath);
    const success = await initApp('live2d-canvas', modelPath);
    console.log('[Model] Load result:', success);
    if (success) {
        startBlinking();
    }

    // ---- Chat UI ----
    const toggleBtn = document.getElementById('chat-toggle');
    const panel = document.getElementById('chat-panel');
    const input = document.getElementById('chat-input');
    const sendBtn = document.getElementById('chat-send');
    const modeBtn = document.getElementById('mode-toggle');

    let panelVisible = false;

    function showPanel() {
        panel.classList.add('visible');
        input.focus();
        panelVisible = true;
        toggleBtn.textContent = '✕';
    }

    function hidePanel() {
        panel.classList.remove('visible');
        panelVisible = false;
        toggleBtn.textContent = '💬';
        input.blur();
    }

    function sendText() {
        const text = input.value.trim();
        if (!text) return;
        input.value = '';
        addChatBubble(text, 'user');
        // Send to Python via bridge
        if (bridge && bridge.on_user_text) {
            bridge.on_user_text(text);
        }
    }

    toggleBtn.addEventListener('click', () => {
        panelVisible ? hidePanel() : showPanel();
    });

    sendBtn.addEventListener('click', sendText);

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            sendText();
        } else if (e.key === 'Escape') {
            hidePanel();
        }
    });

    modeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleResponseMode();
    });

    // Prevent drag-through on the panel
    panel.addEventListener('mousedown', (e) => { e.stopPropagation(); });
    toggleBtn.addEventListener('mousedown', (e) => { e.stopPropagation(); });
    modeBtn.addEventListener('mousedown', (e) => { e.stopPropagation(); });
});

// --- Cleanup ---
window.addEventListener('beforeunload', () => {
    stopIdleMotions();
    stopBlinking();
    if (app) {
        app.destroy(true);
        app = null;
    }
});
