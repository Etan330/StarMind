/**
 * Scroll-based time picker with looping wheels.
 * Replaces all input[type="time"] with a scroll wheel UI.
 * Default time = current time. Wheels loop infinitely.
 */
(function () {
  const CSS = `
.scroll-time-picker{display:inline-flex;align-items:center;gap:2px;border:1px solid #ddd;border-radius:8px;padding:4px 8px;background:#fff;cursor:pointer;user-select:none;font-size:0.9rem;}
.scroll-time-picker .tp-display{min-width:48px;text-align:center;font-variant-numeric:tabular-nums;}
.tp-overlay{position:fixed;inset:0;background:rgba(0,0,0,0.25);display:flex;align-items:center;justify-content:center;z-index:9999;}
.tp-modal{background:#fff;border-radius:16px;padding:24px;box-shadow:0 8px 32px rgba(0,0,0,0.12);display:flex;flex-direction:column;align-items:center;gap:16px;}
.tp-wheels{display:flex;gap:12px;align-items:center;}
.tp-wheel{height:150px;width:52px;overflow-y:scroll;scroll-snap-type:y mandatory;-webkit-overflow-scrolling:touch;border:1px solid #eee;border-radius:8px;text-align:center;}
.tp-wheel::-webkit-scrollbar{display:none;}
.tp-wheel-item{height:50px;line-height:50px;scroll-snap-align:center;font-size:1.1rem;color:#bbb;transition:color 0.1s;}
.tp-wheel-item.active{color:#215f52;font-weight:600;font-size:1.2rem;}
.tp-sep{font-size:1.4rem;font-weight:bold;color:#333;}
.tp-done{padding:8px 24px;border:none;background:#215f52;color:#fff;border-radius:8px;font-size:0.9rem;cursor:pointer;}
.tp-done:hover{background:#1a4d42;}
`;

  if (!document.getElementById('tp-styles')) {
    const s = document.createElement('style');
    s.id = 'tp-styles';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function pad(n) { return String(n).padStart(2, '0'); }

  // Create a looping wheel: 3 copies of items so user can scroll continuously
  function createWheel(count) {
    const el = document.createElement('div');
    el.className = 'tp-wheel';
    el.dataset.count = count;
    let html = '';
    // 3 copies for looping effect
    for (let copy = 0; copy < 3; copy++) {
      for (let i = 0; i < count; i++) {
        html += `<div class="tp-wheel-item" data-v="${i}" data-copy="${copy}">${pad(i)}</div>`;
      }
    }
    el.innerHTML = html;
    return el;
  }

  function scrollToValue(wheel, val) {
    const count = parseInt(wheel.dataset.count);
    // Scroll to middle copy
    const targetIdx = count + val;
    const items = wheel.querySelectorAll('.tp-wheel-item');
    if (items[targetIdx]) {
      wheel.scrollTop = items[targetIdx].offsetTop - wheel.offsetTop - 50;
    }
  }

  function getSelectedValue(wheel) {
    const count = parseInt(wheel.dataset.count);
    const centerY = wheel.scrollTop + 75;
    let closest = 0, minDist = Infinity;
    wheel.querySelectorAll('.tp-wheel-item').forEach(item => {
      const itemCenter = item.offsetTop - wheel.offsetTop + 25;
      const dist = Math.abs(itemCenter - (wheel.scrollTop + 75));
      if (dist < minDist) { minDist = dist; closest = parseInt(item.dataset.v); }
    });
    return closest;
  }

  function highlightActive(wheel) {
    const val = getSelectedValue(wheel);
    wheel.querySelectorAll('.tp-wheel-item').forEach(item => {
      item.classList.toggle('active', parseInt(item.dataset.v) === val);
    });
  }

  // Loop: when scrolled near top or bottom, jump to middle copy
  function handleLoop(wheel) {
    const count = parseInt(wheel.dataset.count);
    const itemHeight = 50;
    const totalOneSet = count * itemHeight;
    const scrollPos = wheel.scrollTop;

    if (scrollPos < totalOneSet * 0.3) {
      // Near top → jump to middle
      wheel.scrollTop = scrollPos + totalOneSet;
    } else if (scrollPos > totalOneSet * 1.7) {
      // Near bottom → jump to middle
      wheel.scrollTop = scrollPos - totalOneSet;
    }
  }

  function openPicker(display, hiddenInput) {
    // Default to current time
    const now = new Date();
    const currentH = now.getHours();
    const currentM = now.getMinutes();
    const savedVal = hiddenInput.value;
    const [h, m] = savedVal ? savedVal.split(':').map(Number) : [currentH, currentM];

    const overlay = document.createElement('div');
    overlay.className = 'tp-overlay';
    const modal = document.createElement('div');
    modal.className = 'tp-modal';

    const wheels = document.createElement('div');
    wheels.className = 'tp-wheels';
    const hWheel = createWheel(24);
    const mWheel = createWheel(60);
    const sep = document.createElement('span');
    sep.className = 'tp-sep';
    sep.textContent = ':';
    wheels.append(hWheel, sep, mWheel);

    const done = document.createElement('button');
    done.className = 'tp-done';
    done.textContent = '确定';

    modal.append(wheels, done);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    setTimeout(() => {
      scrollToValue(hWheel, h);
      scrollToValue(mWheel, m);
      highlightActive(hWheel);
      highlightActive(mWheel);
    }, 30);

    let loopTimeout;
    function onScroll(wheel) {
      highlightActive(wheel);
      clearTimeout(loopTimeout);
      loopTimeout = setTimeout(() => handleLoop(wheel), 150);
    }

    hWheel.addEventListener('scroll', () => onScroll(hWheel));
    mWheel.addEventListener('scroll', () => onScroll(mWheel));

    done.addEventListener('click', () => {
      const hv = getSelectedValue(hWheel);
      const mv = getSelectedValue(mWheel);
      const val = pad(hv) + ':' + pad(mv);
      hiddenInput.value = val;
      display.textContent = val;
      overlay.remove();
    });

    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  }

  function replaceTimeInput(input) {
    // Default to current time if no value set
    const now = new Date();
    const defaultVal = pad(now.getHours()) + ':' + pad(now.getMinutes());
    const val = input.value || defaultVal;

    const wrapper = document.createElement('div');
    wrapper.className = 'scroll-time-picker';
    const display = document.createElement('span');
    display.className = 'tp-display';
    display.textContent = val;
    wrapper.appendChild(display);

    const hidden = document.createElement('input');
    hidden.type = 'hidden';
    hidden.name = input.name;
    hidden.id = input.id;
    hidden.value = val;

    wrapper.addEventListener('click', () => openPicker(display, hidden));

    input.parentNode.insertBefore(wrapper, input);
    input.parentNode.insertBefore(hidden, input);
    input.remove();
  }

  function init() {
    document.querySelectorAll('input[type="time"]').forEach(replaceTimeInput);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.initTimePickers = init;
})();
