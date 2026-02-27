/**
 * LLM Chess Match – Web UI.
 * Subscribes to GET /api/events (SSE) for state updates; backend sends an event when a move is made.
 * Fetches /api/state only when notified, so the board updates only on new moves (no periodic blinking).
 * Stockfish evaluation from /api/analyze (server) or WASM fallback.
 */
(function () {
  'use strict';

  const API_BASE = (typeof window.CHESSMATCH_API_BASE !== 'undefined' ? window.CHESSMATCH_API_BASE : '') || '';
  const STOCKFISH_WORKER_URL = '/static/stockfish-nnue-16-single.js';
  const POLL_MS_ACTIVE = 2000;
  const POLL_MS_IDLE = 5000;
  const DEFAULT_ANALYSIS_DEPTH = 15;
  const defaultFen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
  var PIECE_BASE = 'https://raw.githubusercontent.com/oakmac/chessboardjs/master/website/img/chesspieces/wikipedia/';

  let boardEl = null;
  let game = null;
  let stockfishWorker = null;
  let analysisPending = false;
  let serverStockfishAvailable = null;
  let stateEventSource = null;
  let pollTimer = null;
  let tickInterval = null;
  let lastStateForHeader = null;
  let currentTimers = { whiteSeconds: null, blackSeconds: null };
  let currentGameId = null;
  let lastFen = null;
  let lastMoveCount = 0;
  let lastEvalFen = null;
  let lastMoveLogLength = 0;
  let lastGameStatusText = '';
  let lastMoveHistoryText = '';
  const isDeployed = !!API_BASE;

  function el(id) {
    return document.getElementById(id);
  }

  function fenToPiecePlacement(fen) {
    var part = (fen || '').trim().split(/\s/)[0] || '';
    var ranks = part.split('/');
    if (ranks.length !== 8) return [];
    var grid = [];
    for (var r = 0; r < 8; r++) {
      var row = [];
      var s = ranks[r] || '';
      for (var i = 0; i < s.length; i++) {
        var c = s[i];
        if (/[1-8]/.test(c)) {
          for (var k = 0; k < parseInt(c, 10); k++) row.push(null);
        } else if (/[KQRBNPkqrbnp]/.test(c)) {
          row.push(c);
        }
      }
      while (row.length < 8) row.push(null);
      grid.push(row.slice(0, 8));
    }
    return grid;
  }

  function pieceToImage(pieceChar) {
    if (!pieceChar) return null;
    var p = pieceChar.toUpperCase();
    var color = pieceChar === pieceChar.toUpperCase() ? 'w' : 'b';
    return color + p;
  }

  function squareToPixel(sq) {
    var file = sq.charCodeAt(0) - 97;
    var rank = parseInt(sq.charAt(1), 10);
    var rowIdx = 8 - rank;
    var cellSize = (boardEl ? boardEl.offsetWidth : 512) / 8;
    return { left: file * cellSize + 3, top: rowIdx * cellSize + 3 };
  }

  function getMoveFromFenDiff(oldFen, newFen) {
    var oldGrid = fenToPiecePlacement(oldFen);
    var newGrid = fenToPiecePlacement(newFen);
    if (!oldGrid.length || !newGrid.length) return null;
    var files = 'abcdefgh';
    var emptied = [];
    var filled = [];
    var replaced = [];
    for (var rank = 8; rank >= 1; rank--) {
      var rowIdx = 8 - rank;
      for (var f = 0; f < 8; f++) {
        var sq = files[f] + rank;
        var oldP = (oldGrid[rowIdx] && oldGrid[rowIdx][f]) || null;
        var newP = (newGrid[rowIdx] && newGrid[rowIdx][f]) || null;
        if (oldP !== newP) {
          if (oldP && !newP) emptied.push({ sq: sq, piece: oldP });
          else if (!oldP && newP) filled.push({ sq: sq, piece: newP });
          else if (oldP && newP) replaced.push({ sq: sq, oldPiece: oldP, newPiece: newP });
        }
      }
    }
    var fromSq = null, toSq = null, pieceChar = null;
    if (emptied.length === 1 && filled.length === 1 && replaced.length === 0) {
      fromSq = emptied[0].sq;
      toSq = filled[0].sq;
      pieceChar = filled[0].piece;
    } else if (emptied.length === 2 && filled.length === 1 && replaced.length === 0) {
      toSq = filled[0].sq;
      pieceChar = filled[0].piece;
      fromSq = emptied[0].piece === pieceChar ? emptied[0].sq : emptied[1].sq;
    } else if (emptied.length === 1 && filled.length === 2 && replaced.length === 0) {
      fromSq = emptied[0].sq;
      var movedPiece = emptied[0].piece;
      if (filled[0].piece === movedPiece) { toSq = filled[0].sq; pieceChar = filled[0].piece; }
      else { toSq = filled[1].sq; pieceChar = filled[1].piece; }
    } else if (replaced.length === 1 && emptied.length === 1 && filled.length === 0) {
      fromSq = emptied[0].sq;
      toSq = replaced[0].sq;
      pieceChar = replaced[0].newPiece;
    } else {
      return null;
    }
    if (!fromSq || !toSq || fromSq === toSq || !pieceChar) return null;
    return { from: fromSq, to: toSq, piece: pieceChar };
  }

  function updateBoardDom(fen) {
    if (!boardEl) return;
    if (game) game.load(fen);
    var grid = fenToPiecePlacement(fen || defaultFen);
    if (grid.length === 0) return;
    var files = 'abcdefgh';
    for (var rank = 8; rank >= 1; rank--) {
      var rowIdx = 8 - rank;
      for (var f = 0; f < 8; f++) {
        var sq = files[f] + rank;
        var squareDiv = boardEl.querySelector('[data-square="' + sq + '"]');
        if (!squareDiv) continue;
        var pieceContainer = squareDiv.querySelector('.piece');
        if (!pieceContainer) continue;
        pieceContainer.innerHTML = '';
        var pieceChar = (grid[rowIdx] && grid[rowIdx][f]) || null;
        if (pieceChar) {
          var imgName = pieceToImage(pieceChar);
          if (imgName) {
            var img = document.createElement('img');
            img.src = PIECE_BASE + imgName + '.png';
            img.alt = pieceChar;
            img.className = 'piece-img';
            img.setAttribute('draggable', 'false');
            pieceContainer.appendChild(img);
          }
        }
      }
    }
  }

  function setFen(fen, oldFen) {
    if (!fen || !fen.trim()) fen = defaultFen;
    if (!boardEl) return;
    if (!oldFen || oldFen === fen) {
      updateBoardDom(fen);
      highlightLastMove(null, null);
      return;
    }
    var move = getMoveFromFenDiff(oldFen, fen);
    if (!move) {
      updateBoardDom(fen);
      return;
    }
    highlightLastMove(move.from, move.to);
    var fromSquare = boardEl.querySelector('[data-square="' + move.from + '"]');
    var pieceContainer = fromSquare ? fromSquare.querySelector('.piece') : null;
    var img = pieceContainer ? pieceContainer.querySelector('img') : null;
    if (!img) {
      if (game) game.load(fen);
      updateBoardDom(fen);
      return;
    }
    if (game) game.load(fen);
    var fromPx = squareToPixel(move.from);
    var toPx = squareToPixel(move.to);
    var overlay = document.createElement('div');
    overlay.className = 'piece-move-overlay';
    img.style.left = fromPx.left + 'px';
    img.style.top = fromPx.top + 'px';
    pieceContainer.removeChild(img);
    overlay.appendChild(img);
    function cleanup() {
      overlay.removeEventListener('transitionend', onEnd);
      overlay.remove();
      updateBoardDom(fen);
      highlightLastMove(move.from, move.to);
    }
    function onEnd() {
      cleanup();
    }
    overlay.addEventListener('transitionend', onEnd);
    boardEl.appendChild(overlay);
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        img.style.left = toPx.left + 'px';
        img.style.top = toPx.top + 'px';
      });
    });
    setTimeout(function () {
      if (overlay.parentNode) onEnd();
    }, 350);
  }

  function renderBoard() {
    var container = el('board');
    if (!container) return;
    game = new Chess();
    boardEl = container;
    container.innerHTML = '';
    container.className = 'chess-board';
    var files = 'abcdefgh';
    var light = true;
    for (var rank = 8; rank >= 1; rank--) {
      for (var f = 0; f < 8; f++) {
        var sq = files[f] + rank;
        var square = document.createElement('div');
        square.className = 'square ' + (light ? 'square-light' : 'square-dark');
        square.setAttribute('data-square', sq);
        square.setAttribute('role', 'gridcell');
        var piece = document.createElement('div');
        piece.className = 'piece';
        square.appendChild(piece);
        container.appendChild(square);
        light = !light;
      }
      light = !light;
    }
    var ranksEl = el('board-ranks');
    if (ranksEl) {
      ranksEl.innerHTML = '';
      for (var r = 8; r >= 1; r--) {
        var span = document.createElement('span');
        span.textContent = r;
        ranksEl.appendChild(span);
      }
    }
    var filesEl = el('board-files');
    if (filesEl) {
      filesEl.innerHTML = '';
      for (var i = 0; i < 8; i++) {
        var span = document.createElement('span');
        span.textContent = files[i];
        filesEl.appendChild(span);
      }
    }
    setFen(defaultFen);
  }

  function hasGame(state) {
    return !!(state && (state.whiteName || state.blackName));
  }

  function formatTimeRemaining(seconds) {
    if (seconds == null || seconds === undefined || seconds < 0 || seconds === Infinity) return '\u221E';
    var s = Math.max(0, Math.floor(seconds));
    var m = Math.floor(s / 60);
    s = s % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  /** Update only the timer spans. Called every second from /api/tick – never from /api/state. */
  function updateTimerDisplays(whiteSeconds, blackSeconds) {
    var matchHeader = el('match-header');
    if (!matchHeader) return;
    var timers = matchHeader.querySelectorAll('.timer');
    if (timers.length >= 2) {
      timers[0].textContent = formatTimeRemaining(whiteSeconds);
      timers[1].textContent = formatTimeRemaining(blackSeconds);
    }
  }

  function renderGameInfo(state) {
    var statusEl = el('game-status');
    var matchHeader = el('match-header');
    var historyEl = el('move-history');
    var boardStatusEl = el('board-status');
    var startPanel = el('start-game-panel');

    if (!hasGame(state)) {
      lastMoveLogLength = 0;
      lastGameStatusText = '';
      lastMoveHistoryText = '';
      if (isDeployed) currentGameId = null;
      if (matchHeader) { matchHeader.innerHTML = ''; matchHeader.style.display = 'none'; }
      if (startPanel) startPanel.style.display = 'block';
      if (statusEl) statusEl.textContent = '';
      if (historyEl) historyEl.textContent = '';
      if (boardStatusEl) boardStatusEl.textContent = '';
      renderChatLog(null);
      clearEval();
      return;
    }

    if (matchHeader) matchHeader.style.display = 'flex';
    if (startPanel) startPanel.style.display = 'none';
    if (matchHeader) {
      var whiteName = state.whiteName || 'White';
      var blackName = state.blackName || 'Black';
      var whiteTime = formatTimeRemaining(currentTimers.whiteSeconds);
      var blackTime = formatTimeRemaining(currentTimers.blackSeconds);
      matchHeader.innerHTML =
        '<span class="player white-player">' + escapeHtml(whiteName) +
        ' <span class="timer" title="Time remaining">' + escapeHtml(whiteTime) + '</span></span>' +
        '<span class="vs">vs</span>' +
        '<span class="player black-player">' + escapeHtml(blackName) +
        ' <span class="timer" title="Time remaining">' + escapeHtml(blackTime) + '</span></span>' +
        '<button type="button" class="btn btn-secondary btn-restart-in-header" id="btn-restart-in-header" title="Clear game and start a new one">Restart</button>';
      var headerRestartBtn = document.getElementById('btn-restart-in-header');
      if (headerRestartBtn && !headerRestartBtn._bound) {
        headerRestartBtn._bound = true;
        headerRestartBtn.addEventListener('click', restartGame);
      }
    }

    var statusText = '';
    if (state.isGameOver) {
      statusText = state.winner ? 'Game over – ' + state.winner + ' wins' : 'Game over – draw';
      if (state.terminationReason) statusText += ' (' + state.terminationReason + ')';
      if (statusEl) statusEl.textContent = statusText;
      if (boardStatusEl) boardStatusEl.textContent = 'Game over';
    } else {
      statusText = 'In progress';
      if (statusEl) statusEl.textContent = statusText;
      if (boardStatusEl) boardStatusEl.textContent = state.moveHistory && state.moveHistory.length ? 'Live' : 'Waiting for first move';
    }

    var historyText = '';
    if (state.moveHistory && state.moveHistory.length > 0) {
      var grid = document.createElement('div');
      grid.className = 'move-history-grid';
      var totalMoves = state.moveHistory.length;
      for (var i = 0; i < totalMoves; i += 2) {
        var num = (i / 2) + 1;
        var w = state.moveHistory[i] || '';
        var b = state.moveHistory[i + 1] || '';

        var numEl = document.createElement('span');
        numEl.className = 'move-num';
        numEl.textContent = num + '.';
        grid.appendChild(numEl);

        var wEl = document.createElement('span');
        wEl.className = 'move-cell' + (i === totalMoves - 1 ? ' move-cell-latest' : '');
        wEl.textContent = w;
        grid.appendChild(wEl);

        var bEl = document.createElement('span');
        bEl.className = 'move-cell' + (i + 1 === totalMoves - 1 ? ' move-cell-latest' : '');
        bEl.textContent = b;
        grid.appendChild(bEl);

        historyText += num + '. ' + w + (b ? ' ' + b : '') + ' ';
      }
      historyEl.innerHTML = '';
      historyEl.appendChild(grid);
      historyEl.scrollTop = historyEl.scrollHeight;
    } else {
      historyEl.innerHTML = '';
    }

    var gamePanel = document.querySelector('.game-eval-panel');
    if (gamePanel && (statusText !== lastGameStatusText || historyText !== lastMoveHistoryText)) {
      lastGameStatusText = statusText;
      lastMoveHistoryText = historyText;
      gamePanel.classList.remove('game-panel-updated');
      gamePanel.offsetHeight;
      gamePanel.classList.add('game-panel-updated');
      setTimeout(function () { gamePanel.classList.remove('game-panel-updated'); }, 300);
    }

    var moveLogLen = (state.moveLog && state.moveLog.length) || 0;
    if (moveLogLen !== lastMoveLogLength) {
      lastMoveLogLength = moveLogLen;
      renderChatLog(state.moveLog);
    }
  }

  var lastHighlightFrom = null;
  var lastHighlightTo = null;

  function highlightLastMove(fromSq, toSq) {
    if (lastHighlightFrom) {
      var prev = boardEl && boardEl.querySelector('[data-square="' + lastHighlightFrom + '"]');
      if (prev) prev.classList.remove('square-highlight');
    }
    if (lastHighlightTo) {
      var prev2 = boardEl && boardEl.querySelector('[data-square="' + lastHighlightTo + '"]');
      if (prev2) prev2.classList.remove('square-highlight');
    }
    lastHighlightFrom = fromSq;
    lastHighlightTo = toSq;
    if (fromSq && boardEl) {
      var f = boardEl.querySelector('[data-square="' + fromSq + '"]');
      if (f) f.classList.add('square-highlight');
    }
    if (toSq && boardEl) {
      var t = boardEl.querySelector('[data-square="' + toSq + '"]');
      if (t) t.classList.add('square-highlight');
    }
  }

  function updateEvalBar(scoreCp, mate) {
    var fill = el('eval-bar-fill');
    var label = el('eval-bar-score');
    if (!fill) return;
    var pct = 50;
    var text = '';
    if (mate != null) {
      pct = mate > 0 ? 100 : 0;
      text = 'M' + Math.abs(mate);
    } else if (scoreCp != null) {
      pct = 50 + 50 * (2 / (1 + Math.exp(-0.004 * scoreCp)) - 1);
      pct = Math.max(3, Math.min(97, pct));
      text = (scoreCp >= 0 ? '+' : '') + (scoreCp / 100).toFixed(1);
    }
    fill.style.height = pct + '%';
    if (label) label.textContent = text;
  }

  function clearEval() {
    var out = el('eval-output');
    var det = el('eval-detail');
    if (out) { out.innerHTML = ''; out.classList.remove('loading', 'error'); }
    if (det) det.textContent = '';
    updateEvalBar(0, null);
  }

  function resultFromState(state) {
    if (!state.isGameOver) return '*';
    if (state.winner) {
      var w = state.whiteName || 'White';
      if (state.winner === w) return '1-0';
      return '0-1';
    }
    return '1/2-1/2';
  }

  function exportGame() {
    fetch(stateUrl(), { cache: 'no-store' })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (state) {
        if (!state || !state.moveHistory || state.moveHistory.length === 0) {
          alert('No game to export. Start a game and make at least one move.');
          return;
        }
        var whiteName = state.whiteName || 'White';
        var blackName = state.blackName || 'Black';
        var result = resultFromState(state);
        var pgnLines = [
          '[Event "LLM Chess Match"]',
          '[White "' + (whiteName.replace(/"/g, '\\"')) + '"]',
          '[Black "' + (blackName.replace(/"/g, '\\"')) + '"]',
          '[Result "' + result + '"]',
          '',
          ''
        ];
        var moves = state.moveHistory;
        var moveLine = [];
        for (var i = 0; i < moves.length; i += 2) {
          var num = (i / 2) + 1;
          var w = moves[i] || '';
          var b = moves[i + 1] || '';
          if (b) moveLine.push(num + '. ' + w + ' ' + b);
          else moveLine.push(num + '. ' + w);
        }
        pgnLines.push(moveLine.join(' ') + ' ' + result);
        var pgn = pgnLines.join('\n');

        var chatLines = [];
        var log = state.moveLog || [];
        for (var j = 0; j < log.length; j++) {
          var entry = log[j];
          chatLines.push((entry.side || '') + ' (' + (entry.llmName || '') + '): ' + (entry.move || ''));
          if (entry.explanation) chatLines.push(entry.explanation);
          chatLines.push('');
        }
        var chatTxt = chatLines.join('\n');

        function download(filename, text) {
          var a = document.createElement('a');
          a.href = 'data:text/plain;charset=utf-8,' + encodeURIComponent(text);
          a.download = filename;
          a.style.display = 'none';
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
        }
        download('game.pgn', pgn);
        download('chat.txt', chatTxt);
      })
      .catch(function () { alert('Could not load game state.'); });
  }

  function renderChatLog(moveLog) {
    var container = el('chat-log');
    if (!container) return;
    if (!moveLog || moveLog.length === 0) {
      container.innerHTML = '';
      return;
    }
    var prevScrollTop = container.scrollTop;
    var prevScrollHeight = container.scrollHeight;
    var wasAtTop = prevScrollTop <= 2;
    var html = '';
    for (var i = moveLog.length - 1; i >= 0; i--) {
      var entry = moveLog[i];
      var side = entry.side || '';
      var llmName = entry.llmName || entry.llm_name || '';
      var move = entry.move || '';
      var explanation = entry.explanation || '';
      var sideClass = side === 'White' ? 'chat-entry-white' : 'chat-entry-black';
      var newClass = (i === moveLog.length - 1) ? ' chat-entry-new' : '';
      var headerLabel = side + (llmName ? ' (' + escapeHtml(llmName) + ')' : '') + ': ';
      html += '<div class="chat-entry ' + sideClass + newClass + '">';
      html += '<div class="chat-entry-header">' + headerLabel + '<strong>' + escapeHtml(move) + '</strong></div>';
      if (explanation) {
        html += '<div class="chat-entry-explanation">' + escapeHtml(explanation) + '</div>';
      }
      html += '</div>';
    }
    container.innerHTML = html;
    requestAnimationFrame(function () {
      var newHeight = container.scrollHeight;
      if (wasAtTop) {
        container.scrollTop = 0;
      } else if (newHeight > prevScrollHeight) {
        container.scrollTop = prevScrollTop + (newHeight - prevScrollHeight);
      } else {
        container.scrollTop = prevScrollTop;
      }
      var firstEntry = container.querySelector('.chat-entry-new');
      if (firstEntry) {
        setTimeout(function () {
          firstEntry.classList.remove('chat-entry-new');
        }, 350);
      }
    });
  }

  function stateUrl() {
    var url = API_BASE + '/api/state?_=' + Date.now();
    if (currentGameId) url += '&game_id=' + encodeURIComponent(currentGameId);
    return url;
  }

  function loadState() {
    return fetch(stateUrl(), { cache: 'no-store' })
      .then(function (res) { return res.ok ? res.json() : Promise.reject(new Error('API error')); })
      .then(function (state) {
        setFen(state.fen);
        renderGameInfo(state);
        return state;
      })
      .catch(function () {
        setFen(defaultFen);
        renderGameInfo({});
        return null;
      });
  }

  function onStateUpdate(state) {
    if (!state) {
      setFen(defaultFen);
      renderGameInfo({});
      return;
    }
    var fen = state.fen || '';
    var moveCount = (state.moveHistory && state.moveHistory.length) || 0;
    var moveCountChanged = moveCount !== lastMoveCount;
    var fenChanged = fen !== lastFen;
    var prevFen = lastFen;
    lastFen = fen;
    lastMoveCount = moveCount;

    if (hasGame(state)) {
      if (fenChanged) setFen(fen, prevFen || undefined);
    } else {
      if (fenChanged) setFen(defaultFen);
    }
    lastStateForHeader = state;
    // Merge latest timer values (kept separately from state API) so we don't
    // regress to ∞ when /api/state omits timers.
    var merged = Object.assign({}, state, {
      whiteRemainingSeconds: currentTimers.whiteSeconds,
      blackRemainingSeconds: currentTimers.blackSeconds
    });
    // If timers show time's up, treat as game over for display so we don't flicker
    // back to "Live" when a late /api/state response still has isGameOver false.
    if (hasGame(state) && (merged.whiteRemainingSeconds === 0 || merged.blackRemainingSeconds === 0)) {
      merged.isGameOver = true;
      merged.terminationReason = merged.terminationReason || 'time';
    }
    renderGameInfo(merged);

    if (hasGame(state) && !merged.isGameOver) {
      startTickPolling();
    } else {
      stopTickPolling();
    }

    if (hasGame(state) && moveCountChanged && moveCount > 0 && fen) {
      fetchEvaluation(fen);
    }
  }

  function fetchState() {
    return fetch(stateUrl(), { cache: 'no-store' })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (state) {
        // Ignore stale "no game" state when we're in a game (e.g. out-of-order
        // response from reset or an earlier request). Otherwise we'd overwrite
        // lastStateForHeader and stop tick polling, so clocks would only update on /api/state.
        if (!state) return state;
        if (currentGameId && !hasGame(state)) return state;
        onStateUpdate(state);
        return state;
      })
      .catch(function () { return null; });
  }

  function startTickPolling() {
    if (tickInterval) return;
    tickInterval = setInterval(fetchTick, 1000);
  }

  function stopTickPolling() {
    if (tickInterval) {
      clearInterval(tickInterval);
      tickInterval = null;
    }
  }

  /** Clocks are driven only by /api/tick every second. /api/state must not affect them. */
  function fetchTick() {
    if (!lastStateForHeader || !hasGame(lastStateForHeader)) return;
    var url = API_BASE + '/api/tick';
    if (currentGameId) {
      url += '?game_id=' + encodeURIComponent(currentGameId);
    }
    fetch(url, { cache: 'no-store' })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (tick) {
        if (!tick || !lastStateForHeader) return;
        var wr = typeof tick.whiteRemainingSeconds === 'number' ? tick.whiteRemainingSeconds : null;
        var br = typeof tick.blackRemainingSeconds === 'number' ? tick.blackRemainingSeconds : null;
        if (wr == null && br == null) return;
        currentTimers.whiteSeconds = wr;
        currentTimers.blackSeconds = br;
        updateTimerDisplays(wr, br);
        var gameOverFromTick = tick.isGameOver || wr === 0 || br === 0;
        if (gameOverFromTick) {
          stopTickPolling();
          var merged = Object.assign({}, lastStateForHeader, {
            isGameOver: true,
            whiteRemainingSeconds: wr,
            blackRemainingSeconds: br
          });
          if (wr === 0 || br === 0) merged.terminationReason = 'time';
          if (tick.terminationReason) merged.terminationReason = tick.terminationReason;
          if (tick.winner != null) merged.winner = tick.winner;
          renderGameInfo(merged);
          fetchState();
        }
      })
      .catch(function () { /* ignore tick errors */ });
  }

  function schedulePoll() {
    if (pollTimer) clearTimeout(pollTimer);
    var ms = (lastFen && lastMoveCount >= 0 && !lastGameStatusText.match(/Game over/)) ? POLL_MS_ACTIVE : POLL_MS_IDLE;
    pollTimer = setTimeout(function () {
      pollTimer = null;
      fetchState().then(function () { schedulePoll(); });
    }, ms);
  }

  function connectStateEvents() {
    if (isDeployed) {
      schedulePoll();
      return;
    }
    if (stateEventSource) return;
    var url = (API_BASE || window.location.origin) + '/api/events';
    stateEventSource = new EventSource(url);
    stateEventSource.addEventListener('state_updated', function () {
      fetchState();
    });
    stateEventSource.onerror = function () {
      stateEventSource.close();
      stateEventSource = null;
      // Retry SSE connection after a short delay; no periodic state polling.
      setTimeout(connectStateEvents, 1000);
    };
  }

  function showEvalResult(html, isError, detail) {
    const out = el('eval-output');
    const det = el('eval-detail');
    if (out) {
      out.classList.remove('loading', 'error', 'eval-updated');
      if (isError) out.classList.add('error');
      out.innerHTML = html;
      out.offsetHeight;
      out.classList.add('eval-updated');
      setTimeout(function () { out.classList.remove('eval-updated'); }, 350);
    }
    if (det) {
      det.classList.remove('eval-detail-updated');
      det.textContent = detail || '';
      det.title = (detail && detail.indexOf('Browser engine') !== -1)
        ? 'Stockfish runs in your browser (WebAssembly) when the server engine is not available.'
        : '';
      det.offsetHeight;
      det.classList.add('eval-detail-updated');
      setTimeout(function () { det.classList.remove('eval-detail-updated'); }, 350);
    }
  }

  function showEvalLoading(msg) {
    const out = el('eval-output');
    const det = el('eval-detail');
    if (out) {
      out.classList.add('loading');
      out.classList.remove('error', 'eval-updated');
      out.innerHTML = '<span class="eval-placeholder">' + (msg || 'Analyzing…') + '</span>';
      out.offsetHeight;
      out.classList.add('eval-updated');
      setTimeout(function () { out.classList.remove('eval-updated'); }, 350);
    }
    if (det) det.textContent = '';
  }

  function escapeHtml(s) {
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  function uciToPgn(uci, fen) {
    if (!uci || !game || uci.length < 4) return uci;
    var from = uci.slice(0, 2);
    var to = uci.slice(2, 4);
    try {
      game.load(fen || defaultFen);
      var move = game.move({ from: from, to: to });
      return move ? move.san : uci;
    } catch (e) {
      return uci;
    }
  }

  function formatBestMoveForDisplay(bestMove, fen) {
    if (!bestMove || bestMove === '(none)') return bestMove;
    if (bestMove.length === 4 || bestMove.length === 5) {
      return uciToPgn(bestMove, fen);
    }
    return bestMove;
  }

  function serverAnalyze(fen, depth) {
    fen = (fen || (game && game.fen && game.fen()) || defaultFen).trim();
    depth = depth != null ? depth : DEFAULT_ANALYSIS_DEPTH;
    var params = new URLSearchParams({ fen: fen });
    if (depth) params.set('depth', String(depth));
    return fetch(API_BASE + '/api/analyze?' + params)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (!data.ok) throw new Error(data.error || 'Analysis failed');
        var html = '';
        if (data.mate != null) {
          var mateStr = data.mate > 0 ? 'Mate in ' + data.mate + ' (White)' : 'Mate in ' + (-data.mate) + ' (Black)';
          html += '<span class="score score-mate">' + escapeHtml(mateStr) + '</span>';
        } else if (data.scoreCp != null) {
          var cls = data.scoreCp >= 0 ? 'white-advantage' : 'black-advantage';
          var cp = (data.scoreCp / 100).toFixed(2);
          html += '<span class="score score-number ' + cls + '">' + escapeHtml(cp) + '</span>';
        }
        if (data.bestMove) {
          var bestPgn = formatBestMoveForDisplay(data.bestMove, fen);
          html += (html ? '<br>' : '') + '<span class="best-move">Best move: ' + escapeHtml(bestPgn) + '</span>';
        }
        if (!html) html = 'No result.';
        var detail = '';
        if (data.pv && data.pv.length) detail = 'PV: ' + data.pv.slice(0, 5).join(' ');
        if (data.depth) detail = (detail ? detail + ' · ' : '') + 'Depth ' + data.depth;
        showEvalResult(html || 'No result.', false, detail);
        updateEvalBar(data.scoreCp, data.mate);
        return data;
      });
  }

  function wasmAnalyze(fen, depth) {
    depth = depth != null ? depth : DEFAULT_ANALYSIS_DEPTH;
    fen = (fen || defaultFen).trim();
    if (analysisPending) return Promise.resolve();
    analysisPending = true;
    showEvalLoading('Starting Stockfish (browser)…');

    function finish() {
      analysisPending = false;
    }

    return initStockfish()
      .then(function () {
        showEvalLoading('Analyzing…');
        return new Promise(function (resolve, reject) {
          var lines = [];
          function handler(e) {
            var line = e.data;
            if (typeof line !== 'string') return;
            lines.push(line);
            if (line.startsWith('bestmove')) {
              stockfishWorker.removeEventListener('message', handler);
              resolve(lines);
            }
          }
          stockfishWorker.addEventListener('message', handler);
          stockfishWorker.postMessage('ucinewgame');
          stockfishWorker.postMessage('position fen ' + fen);
          stockfishWorker.postMessage('go depth ' + depth);
          setTimeout(function () {
            if (lines.some(function (l) { return l.startsWith('bestmove'); })) return;
            stockfishWorker.removeEventListener('message', handler);
            resolve(lines);
          }, 30000);
        });
      })
      .then(function (lines) {
        var bestMove = null, score = null, mate = null, scoreDepth = -1;
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i];
          if (line.startsWith('bestmove')) {
            var m = line.match(/bestmove\s+(\S+)/);
            if (m) bestMove = m[1];
          }
          if (line.startsWith('info') && line.indexOf('score') !== -1) {
            var depthMatch = line.match(/\bdepth\s+(\d+)/);
            var lineDepth = depthMatch ? parseInt(depthMatch[1], 10) : 0;
            if (lineDepth >= scoreDepth) {
              var cp = line.match(/score\s+cp\s+(-?\d+)/);
              var mMatch = line.match(/score\s+mate\s+(-?\d+)/);
              if (mMatch) { mate = parseInt(mMatch[1], 10); scoreDepth = lineDepth; }
              else if (cp) { score = parseInt(cp[1], 10); scoreDepth = lineDepth; }
            }
          }
        }
        var blackToMove = (fen || '').trim().split(/\s/)[1] === 'b';
        if (blackToMove) {
          if (score != null) score = -score;
          if (mate != null) mate = -mate;
        }
        var html = '';
        if (mate != null) {
          var mateStr = mate > 0 ? 'Mate in ' + mate + ' (White)' : 'Mate in ' + (-mate) + ' (Black)';
          html += '<span class="score score-mate">' + escapeHtml(mateStr) + '</span>';
        } else if (score != null) {
          var cls = score >= 0 ? 'white-advantage' : 'black-advantage';
          html += '<span class="score score-number ' + cls + '">' + (score / 100).toFixed(2) + '</span>';
        }
        if (bestMove && bestMove !== '(none)') {
          var bestPgn = formatBestMoveForDisplay(bestMove, fen);
          html += (html ? '<br>' : '') + '<span class="best-move">Best move: ' + escapeHtml(bestPgn) + '</span>';
        }
        if (!html) html = 'No result.';
        showEvalResult(html, false, 'Browser engine · Depth ' + depth);
        updateEvalBar(score, mate);
      })
      .catch(function (err) {
        showEvalResult('Stockfish failed: ' + escapeHtml(err.message || String(err)), true, '');
      })
      .then(finish, finish);
  }

  function initStockfish() {
    if (stockfishWorker) return Promise.resolve();
    return new Promise(function (resolve, reject) {
      try {
        stockfishWorker = new Worker(STOCKFISH_WORKER_URL);
        stockfishWorker.onerror = function (e) { reject(e); };
        var resolved = false;
        function done() {
          if (resolved) return;
          resolved = true;
          stockfishWorker.removeEventListener('message', onMsg);
          resolve();
        }
        function onMsg() { done(); }
        stockfishWorker.addEventListener('message', onMsg);
        stockfishWorker.postMessage('uci');
        setTimeout(done, 1500);
      } catch (e) {
        reject(e);
      }
    });
  }

  function fetchEvaluation(fen) {
    if (lastEvalFen === fen) return;
    lastEvalFen = fen;
    var depth = DEFAULT_ANALYSIS_DEPTH;

    function tryServerThenWasm() {
      if (serverStockfishAvailable === true) {
        return serverAnalyze(fen, depth).catch(function () { return wasmAnalyze(fen, depth); });
      }
      if (serverStockfishAvailable === false) {
        return wasmAnalyze(fen, depth);
      }
      return fetch(API_BASE + '/api/stockfish-available')
        .then(function (r) { return r.json(); })
        .then(function (data) {
          serverStockfishAvailable = data.available === true;
          if (serverStockfishAvailable) return serverAnalyze(fen, depth);
          return wasmAnalyze(fen, depth);
        })
        .catch(function () {
          serverStockfishAvailable = false;
          return wasmAnalyze(fen, depth);
        });
    }

    showEvalLoading('Evaluating position…');
    tryServerThenWasm().catch(function (err) {
      showEvalResult('Evaluation failed: ' + escapeHtml(err.message || String(err)), true, '');
    });
  }

  function runManualAnalysis() {
    var fen = (game && game.fen && game.fen()) || defaultFen;
    if (analysisPending) return;
    analysisPending = true;
    showEvalLoading('Analyzing…');
    lastEvalFen = null;

    function tryServerThenWasm() {
      if (serverStockfishAvailable === true) {
        return serverAnalyze(fen).catch(function () { return wasmAnalyze(fen); });
      }
      if (serverStockfishAvailable === false) return wasmAnalyze(fen);
      return fetch(API_BASE + '/api/stockfish-available')
        .then(function (r) { return r.json(); })
        .then(function (data) {
          serverStockfishAvailable = data.available === true;
          if (serverStockfishAvailable) return serverAnalyze(fen);
          return wasmAnalyze(fen);
        })
        .catch(function () {
          serverStockfishAvailable = false;
          return wasmAnalyze(fen);
        });
    }

    tryServerThenWasm()
      .then(function () { analysisPending = false; })
      .catch(function (err) {
        showEvalResult('Analysis failed: ' + escapeHtml(err.message || String(err)), true, '');
        analysisPending = false;
      });
  }

  function loadAdapters() {
    return fetch(API_BASE + '/api/adapters', { cache: 'no-store' })
      .then(function (res) { return res.ok ? res.json() : []; })
      .then(function (adapters) {
        var whiteSel = el('white-adapter');
        var blackSel = el('black-adapter');
        var startPanel = el('start-game-panel');
        var msgEl = el('start-game-message');
        if (!whiteSel || !blackSel) return;
        if (!adapters || adapters.length === 0) {
          if (startPanel) startPanel.style.display = 'block';
          if (msgEl) { msgEl.textContent = 'API not reached. When using S3/static hosting, set API_BASE to your API URL (see README).'; msgEl.className = 'start-game-message'; }
          return;
        }
        if (startPanel) startPanel.style.display = 'block';
        if (msgEl) { msgEl.textContent = ''; msgEl.className = 'start-game-message'; }
        var opts = adapters.map(function (a) {
          return '<option value="' + escapeHtml(a.id) + '">' + escapeHtml(a.name) + '</option>';
        }).join('');
        whiteSel.innerHTML = '<option value="">Choose LLM for white</option>' + opts;
        blackSel.innerHTML = '<option value="">Choose LLM for black</option>' + opts;
        return adapters;
      })
      .catch(function () {
        var startPanel = el('start-game-panel');
        var msgEl = el('start-game-message');
        if (startPanel) startPanel.style.display = 'block';
        if (msgEl) { msgEl.textContent = 'API not reached. When using S3/static hosting, set API_BASE to your API URL (see README).'; msgEl.className = 'start-game-message'; }
      });
  }

  function startGameFromUI() {
    var whiteSel = el('white-adapter');
    var blackSel = el('black-adapter');
    var timeInput = el('time-per-player');
    var retriesInput = el('max-retries');
    var msgEl = el('start-game-message');
    var btn = el('btn-start-game');
    if (!whiteSel || !blackSel) return;
    var hasError = false;
    if (msgEl) { msgEl.textContent = ''; msgEl.className = 'start-game-message'; }
    whiteSel.classList.remove('input-error');
    blackSel.classList.remove('input-error');
    if (timeInput) timeInput.classList.remove('input-error');
    if (retriesInput) retriesInput.classList.remove('input-error');
    var fenInput = el('starting-fen');
    if (fenInput) fenInput.classList.remove('input-error');

    var whiteId = whiteSel.value;
    var blackId = blackSel.value;
    if (!whiteId) {
      hasError = true;
      whiteSel.classList.add('input-error');
    }
    if (!blackId) {
      hasError = true;
      blackSel.classList.add('input-error');
    }
    if (hasError) {
      if (msgEl) { msgEl.textContent = 'Choose LLMs for White and Black.'; msgEl.className = 'start-game-message error'; }
      return;
    }
    if (whiteId === blackId) {
      if (msgEl) { msgEl.textContent = 'White and Black must be different.'; msgEl.className = 'start-game-message error'; }
      whiteSel.classList.add('input-error');
      blackSel.classList.add('input-error');
      return;
    }
    var timeSec = timeInput ? parseFloat(timeInput.value, 10) : 0;
    if (isNaN(timeSec) || timeSec < 0) {
      hasError = true;
      timeSec = 0;
      if (timeInput) timeInput.classList.add('input-error');
    }
    var maxRetries = retriesInput ? parseInt(retriesInput.value, 10) : 0;
    if (isNaN(maxRetries) || maxRetries < 0 || maxRetries > 20) {
      hasError = true;
      if (retriesInput) retriesInput.classList.add('input-error');
    }
    var startingFen = null;
    if (fenInput && fenInput.value) {
      startingFen = fenInput.value.trim();
      if (startingFen === '') startingFen = null;
      if (startingFen) {
        try {
          var tmp = new Chess();
          tmp.load(startingFen);
        } catch (e) {
          hasError = true;
          if (fenInput) fenInput.classList.add('input-error');
          if (msgEl) { msgEl.textContent = 'Starting FEN is invalid. Please provide a valid FEN or leave empty.'; msgEl.className = 'start-game-message error'; }
        }
      }
    }
    if (hasError) return;
    if (msgEl) { msgEl.textContent = 'Starting…'; msgEl.className = 'start-game-message'; }
    if (btn) btn.disabled = true;
    fetch(API_BASE + '/api/game/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        white_llm_id: whiteId,
        black_llm_id: blackId,
        max_retries: maxRetries,
        time_per_player_seconds: timeSec > 0 ? timeSec : null,
        starting_fen: startingFen
      })
    })
      .then(function (res) {
        if (res.status === 202 || res.ok) {
          return res.json().then(function (data) {
            if (data && data.game_id) currentGameId = data.game_id;
            if (msgEl) { msgEl.textContent = 'Game started. Watch the board for moves.'; msgEl.className = 'start-game-message success'; }
            fetchState();
          }).catch(function () {
            if (msgEl) { msgEl.textContent = 'Game started. Watch the board for moves.'; msgEl.className = 'start-game-message success'; }
            fetchState();
          });
        } else {
          var contentType = (res.headers.get('Content-Type') || '').toLowerCase();
          if (contentType.indexOf('application/json') !== -1) {
            return res.json().then(function (data) {
              if (msgEl) { msgEl.textContent = data.detail || 'Failed to start game.'; msgEl.className = 'start-game-message error'; }
            }).catch(function () {
              if (msgEl) { msgEl.textContent = 'Failed to start game.'; msgEl.className = 'start-game-message error'; }
            });
          }
          if (msgEl) { msgEl.textContent = 'Failed to start game (status ' + res.status + ').'; msgEl.className = 'start-game-message error'; }
        }
      })
      .catch(function (err) {
        if (msgEl) { msgEl.textContent = 'Network error. Is the game server running? Check the console for CORS errors.'; msgEl.className = 'start-game-message error'; }
      })
      .then(function () {
        if (btn) btn.disabled = false;
      });
  }

  function restartGame() {
    var msgEl = el('start-game-message');
    var btnRestart = el('btn-restart-game');
    var headerRestart = el('btn-restart-in-header');
    if (msgEl) { msgEl.textContent = 'Resetting…'; msgEl.className = 'start-game-message'; }
    // Optimistically clear local UI so the reset feels instant, even if the
    // backend (Lambda + S3 + CloudFront) takes a moment to complete.
    currentGameId = null;
    lastFen = null;
    lastMoveCount = 0;
    lastMoveLogLength = 0;
    lastEvalFen = null;
    setFen(defaultFen);
    renderGameInfo({});
    if (btnRestart) btnRestart.disabled = true;
    if (headerRestart) headerRestart.disabled = true;
    var body = currentGameId ? JSON.stringify({ game_id: currentGameId }) : undefined;
    fetch(API_BASE + '/api/game/reset', {
      method: 'POST',
      headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
      body: body
    })
      .then(function (res) {
        var contentType = (res.headers.get('Content-Type') || '').toLowerCase();
        var isJson = contentType.indexOf('application/json') !== -1;
        if (res.ok) {
          if (isJson) {
            return res.json().then(function (data) {
              if (msgEl) { msgEl.textContent = 'Game cleared. Start a new game when ready.'; msgEl.className = 'start-game-message success'; }
              // We already cleared the local UI; just ensure state is synced from backend.
              fetchState();
            });
          }
          // 200 but no JSON (e.g. some proxies): still refresh state so UI can recover
          if (msgEl) { msgEl.textContent = 'Game cleared. Start a new game when ready.'; msgEl.className = 'start-game-message success'; }
          return fetchState();
        }
        return res.text().then(function (text) {
          var detail = '';
          try {
            var data = JSON.parse(text);
            detail = data.detail || data.message || '';
          } catch (e) {
            detail = text || ('Status ' + res.status);
          }
          if (msgEl) { msgEl.textContent = detail || 'Failed to reset (status ' + res.status + ').'; msgEl.className = 'start-game-message error'; }
        }).catch(function () {
          if (msgEl) { msgEl.textContent = 'Failed to reset (status ' + res.status + ').'; msgEl.className = 'start-game-message error'; }
        });
      })
      .catch(function (err) {
        if (msgEl) {
          msgEl.textContent = 'Network error. Is the API server running? Check the browser console for CORS or connection errors.';
          msgEl.className = 'start-game-message error';
        }
      })
      .then(function () {
        if (btnRestart) btnRestart.disabled = false;
        if (headerRestart) headerRestart.disabled = false;
      });
  }

  function resetOnFirstLoad() {
    // For a fresh page load we always want a clean slate: clear any previous
    // local state and ask the backend to reset. This applies both locally and
    // on AWS (where reset targets the default state key when no game_id is set).
    currentGameId = null;
    lastFen = null;
    lastMoveCount = 0;
    lastMoveLogLength = 0;
    lastEvalFen = null;
    setFen(defaultFen);
    renderGameInfo({});
    fetch(API_BASE + '/api/game/reset', {
      method: 'POST',
      headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' }
    })
      .catch(function () {
        // Ignore reset errors on initial load; the user can still start a new game.
      })
      .then(function () {
        // After reset (or even if it failed), load whatever state the backend exposes.
        return fetchState();
      });
  }

  function resetOnUnload() {
    // When the page is being closed, request a reset so the backend can
    // cancel any running game and stop calling LLMs.
    var url = API_BASE + '/api/game/reset';
    var bodyObj = currentGameId ? { game_id: currentGameId } : {};
    var payload = JSON.stringify(bodyObj);
    if (navigator.sendBeacon) {
      try {
        var blob = new Blob([payload], { type: 'application/json' });
        navigator.sendBeacon(url, blob);
        return;
      } catch (e) {
        // Fallback to fetch below.
      }
    }
    try {
      fetch(url, {
        method: 'POST',
        headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
        body: payload,
        keepalive: true
      }).catch(function () { /* ignore */ });
    } catch (e) {
      // Ignore errors on unload.
    }
  }

  if (el('btn-export')) el('btn-export').addEventListener('click', exportGame);
  if (el('btn-start-game')) el('btn-start-game').addEventListener('click', startGameFromUI);

  loadAdapters();

  function sizeBoardToViewport() {
    var header = document.querySelector('.header');
    var headerH = header ? header.offsetHeight : 80;
    var available = window.innerHeight - headerH - 32;
    var boardSize = Math.min(Math.max(Math.floor(available - 40), 320), 680);
    document.documentElement.style.setProperty('--board-size', boardSize + 'px');
  }
  sizeBoardToViewport();
  window.addEventListener('resize', sizeBoardToViewport);

  renderBoard();
  connectStateEvents();
  resetOnFirstLoad();
  if (typeof window !== 'undefined') {
    window.addEventListener('beforeunload', resetOnUnload);
    window.addEventListener('pagehide', resetOnUnload);
  }
})();
