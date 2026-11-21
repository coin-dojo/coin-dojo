/* #227 harness script: drives the keyboard with synthetic key events and reports facts as JSON in <pre id=result>. */
(function () {
  var R = {}, seen = [];
  var logs = 0;
  ['log', 'info', 'warn', 'error', 'debug'].forEach(function (m) { var o = console[m]; console[m] = function () { logs++; return o.apply(console, arguments); }; });
  /* the "page": a listener that would see every key reaching it */
  window.addEventListener('keydown', function (e) { seen.push(e.key); }, false);
  /* the nine dedicated keys stickmouse sends, matched by e.code; the key value is what xkb gives (never F13..F24) */
  var MAP = { F20: 'F13', OPEN: 'F13', ArrowUp: 'F14', ArrowDown: 'F15', ArrowLeft: 'F16', ArrowRight: 'F17', ' ': 'F18',
              Backspace: 'F19', Enter: 'F23', Escape: 'F24' };
  function key(k, target) {
    var e = new KeyboardEvent('keydown', { key: 'Unidentified', code: MAP[k] || k, bubbles: true, cancelable: true });
    (target || document.activeElement || document.body).dispatchEvent(e);
    return e;
  }
  /* an ordinary keyboard key (a USB keyboard): natural key and code */
  function nat(k) {
    var e = new KeyboardEvent('keydown', { key: k, code: k === ' ' ? 'Space' : k.length === 1 ? 'Key' + k.toUpperCase() : k, bubbles: true, cancelable: true });
    (document.activeElement || document.body).dispatchEvent(e);
    return e;
  }
  var user = document.getElementById('user'), pass = document.getElementById('pass');
  var osk = function () { return document.getElementById('hk-osk'); };
  var isOpen = function () { return !!osk() && osk().classList.contains('hk-osk-open') && getComputedStyle(osk()).display !== 'none'; };
  user.focus();

  /* 1. hidden: no key is touched (not prevented, not stopped, nothing typed) */
  var hidden = ['ArrowRight', 'ArrowDown', ' ', 'Backspace', 'Enter', 'Escape', 'a'].map(function (k) { return nat(k); });
  R.hidden_untouched = hidden.every(function (e) { return !e.defaultPrevented; }) && seen.length === 7 && user.value === '';
  R.hidden_no_element = !osk();

  /* 2. F20 opens it; the login placement is the left panel under the logo */
  seen.length = 0;
  var f20 = key('F20');
  R.opened = isOpen() && f20.defaultPrevented;
  var r = osk().getBoundingClientRect(), logo = document.querySelector('.logo').getBoundingClientRect();
  R.login_class = osk().classList.contains('hk-osk-login');
  R.in_left_panel = r.left >= 0 && r.right <= 637 && r.top >= logo.bottom && r.bottom <= 1080;
  R.focus_still_in_field = document.activeElement === user;

  /* 3. typing: initial focus is q; Space types it, Right + Space types w, Backspace deletes */
  key('F20');                       // a second open key while open does nothing harmful
  seen.length = 0;
  var sp = key(' ');
  key('ArrowRight'); key(' ');
  R.typed = user.value;
  key('Backspace');
  R.after_backspace = user.value;
  R.page_saw_nothing = seen.length === 0 && sp.defaultPrevented;

  /* 4. shift + symbols layer: SHIFT is row 4 (bottom), col 0; go down 4 from q row 1 -> row 5 */
  user.value = '';
  var focusTxt = function () { var f = osk().querySelector('.hk-osk-focus'); return f && f.textContent; };
  R.focus_w = focusTxt();
  key('ArrowDown'); key('ArrowDown'); key('ArrowDown');
  R.focus_row5 = focusTxt();
  key(' ');                         // SHIFT toggles the capital layer
  key('ArrowUp'); key('ArrowUp'); key('ArrowUp'); key('ArrowUp');   // up to the digits row (row 0)
  key('ArrowDown');                 // row 1: a capital Q
  R.shift_layer = focusTxt();
  key(' ');
  R.shift_typed = user.value;       // 'Q'; the shift drops back to lower case after one key

  /* 5. into the password field; its text never reaches a console, a storage or a request */
  pass.focus();
  key('Escape');
  R.closed_by_escape = !isOpen();
  var st0 = (function () { try { return localStorage.length + ':' + document.cookie; } catch (e) { return 'n/a'; } })();
  key('F20'); key(' ');
  R.password_typed = pass.value.length === 1;
  var st1 = (function () { try { return localStorage.length + ':' + document.cookie; } catch (e) { return 'n/a'; } })();
  R.no_storage = st0 === st1;
  R.no_console = logs === 0;

  /* 6. START (F23, #262): closes it, keeps the focus in the field, and makes NO Enter of its own (stickmouse sends a
     real one next); a keyboard Enter then passes untouched, closed or open */
  var submits = 0;
  document.getElementById('f').addEventListener('submit', function (e) { e.preventDefault(); submits++; });
  user.focus();
  key('ArrowUp'); key('ArrowUp'); key('ArrowUp'); key('ArrowUp');
  var enters = 0;
  var countEnter = function (e) { if (e.key === 'Enter' || e.code === 'Enter') { enters++; } };
  user.addEventListener('keydown', countEnter, true); user.addEventListener('keypress', countEnter, true); user.addEventListener('keyup', countEnter, true);
  seen.length = 0;
  key('Enter');
  R.enter_closed = !isOpen();
  R.f23_makes_no_enter = enters === 0 && submits === 0 && seen.length === 0;
  R.f23_keeps_focus = document.activeElement === user;
  var ne = nat('Enter');
  R.real_enter_passes_closed = !ne.defaultPrevented && seen.indexOf('Enter') >= 0;
  key('F20'); seen.length = 0;
  ne = nat('Enter');
  R.real_enter_passes_open = !ne.defaultPrevented && seen.indexOf('Enter') >= 0;
  R.real_enter_keeps_open = isOpen();
  key('Escape');
  /* 6b. a click moved the focus to a button while the old field is still there: F23 puts it back on the field */
  key('F20');
  document.getElementById('go').focus();
  key('Enter');
  R.f23_refocuses_field = document.activeElement === user && !isOpen();
  user.removeEventListener('keydown', countEnter, true); user.removeEventListener('keypress', countEnter, true); user.removeEventListener('keyup', countEnter, true);

  /* 7. closed again: keys pass untouched */
  var after = ['ArrowLeft', ' ', 'Backspace'].map(function (k) { return nat(k); });
  R.closed_untouched = after.every(function (e) { return !e.defaultPrevented; });


  /* 7b. macros: MACROS page, a line types into the field and is NOT sent; tabs; SAVE (never from a password); FORGET */
  user.value = ''; user.focus();
  submits = 0;
  key('F20');
  var go = function (seq) { seq.forEach(function (k) { key(k); }); };
  go(['ArrowDown', 'ArrowDown', 'ArrowDown', 'ArrowDown', 'ArrowRight', ' ']);       // row 5 (extra): CLOSE, MACROS -> open the page
  R.macro_page_focus = focusTxt();
  key(' ');
  R.macro_typed = user.value;
  R.macro_not_sent = submits === 0 && isOpen();
  go(['ArrowUp', 'ArrowRight', ' ']);                                                  // tabs row: AFTER MATCH
  go(['ArrowDown']);
  R.tab_after_first = focusTxt();
  go(['ArrowUp', 'ArrowRight', ' ', 'ArrowDown']);                                      // STATUS
  R.tab_status_first = focusTxt();
  user.value = '';
  user.focus(); document.execCommand('insertText', false, 'my own line');
  go(['ArrowDown', 'ArrowDown', 'ArrowRight', ' ']);                                    // bottom row: KEYS, SAVE
  R.save_hint = osk().querySelector('.hk-osk-hint').textContent;
  R.saved_file = window.__files[Object.keys(window.__files)[0]];
  R.mine_shows_it = Array.prototype.some.call(osk().querySelectorAll('.hk-osk-macro'), function (e) { return e.textContent === 'my own line'; });
  var writes = window.__writes;
  var toSave = function () { for (var i = 0; i < 12 && focusTxt() !== 'SAVE'; i++) { key(focusTxt() === 'KEYS' ? 'ArrowRight' : 'ArrowDown'); } };
  pass.value = ''; pass.focus();
  document.execCommand('insertText', false, 'hunter2');
  R.password_value_typed = pass.value === 'hunter2';
  toSave(); key(' ');
  R.password_not_saved = window.__writes === writes && window.__files[Object.keys(window.__files)[0]].indexOf('hunter2') < 0;
  R.password_flash = osk().querySelector('.hk-osk-hint').textContent;
  /* FORGET: focus the saved line (tab MINE), then FORGET */
  user.focus();
  go(['ArrowUp']);
  R.on_mine_line = focusTxt();
  go(['ArrowDown', 'ArrowRight', 'ArrowRight', ' ']);
  R.forgotten = window.__files[Object.keys(window.__files)[0]].trim() === '[]';
  key('Escape');

  /* 7c. stickmouse owns open/closed: the on-screen ENTER/CLOSE keys do not close it; a key while hidden reopens it;
     a cancel while hidden stays hidden; a blur (a game took the focus) hides it; a script-made key does nothing */
  user.focus();
  key('F20');
  var gotoRow = function (txt) { for (var i = 0; i < 14 && focusTxt() !== txt; i++) { key(focusTxt() && i < 7 ? 'ArrowDown' : 'ArrowRight'); } };
  go(['ArrowDown', 'ArrowDown', 'ArrowDown', 'ArrowDown', 'ArrowDown']);     // the extra row (CLOSE, MACROS, ENTER)
  var onClose = focusTxt();
  key(' ');
  R.close_key_hint = osk().querySelector('.hk-osk-hint').textContent;
  R.close_key_keeps_open = onClose === 'CLOSE' && isOpen();
  go(['ArrowRight', 'ArrowRight']);
  key(' ');
  R.enter_key_hint = osk().querySelector('.hk-osk-hint').textContent;
  R.enter_key_keeps_open = isOpen() && submits === 0;
  window.dispatchEvent(new Event('blur'));
  R.blur_hides = !isOpen();
  key('ArrowRight');
  R.key_while_hidden_reopens = isOpen();
  key('Escape');
  key('Escape');
  R.cancel_while_hidden_stays = !isOpen();
  var synthetic = new KeyboardEvent('keydown', { key: 'Unidentified', code: 'F13', bubbles: true, cancelable: true });
  R.untrusted_note = window.__trustChecked === true ? 'checked' : 'harness';

  /* 8. a page with no password field (the lobby) docks bottom centre */
  pass.parentNode.removeChild(pass);
  key('F20');
  var q = osk().getBoundingClientRect();
  R.lobby_class_off = !osk().classList.contains('hk-osk-login');
  R.lobby_bottom_centre = Math.abs((q.left + q.right) / 2 - 720) < 2 && q.bottom <= 1080 && q.bottom > 1000;
  R.in_column = q.left >= 0 && q.right <= 1440;
  key('Escape');

  var pre = document.createElement('pre'); pre.id = 'result';
  pre.textContent = JSON.stringify(R);
  document.body.appendChild(pre);
}());
