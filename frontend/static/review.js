/**
 * Review game page: upload PGN + chat.txt, step through moves and see LLM comments.
 */
(function () {
  'use strict';

  var defaultFen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
  var PIECE_BASE = 'https://raw.githubusercontent.com/oakmac/chessboardjs/master/website/img/chesspieces/wikipedia/';

  var game = null;
  var boardEl = null;
  var fens = [];
  var moveHistory = [];
  var moveLog = [];
  var currentIndex = 0;
  var lastRenderedIndex = -1;

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

  var lastHighlightFrom = null;
  var lastHighlightTo = null;

  function squareToPixel(sq) {
    var file = sq.charCodeAt(0) - 97;
    var rank = parseInt(sq.charAt(1), 10);
    var rowIdx = 8 - rank;
    var cellSize = (boardEl ? boardEl.offsetWidth : 512) / 8;
    return { left: file * cellSize + 3, top: rowIdx * cellSize + 3 };
  }

  function highlightLastMove(fromSq, toSq) {
    if (lastHighlightFrom && boardEl) {
      var prev = boardEl.querySelector('[data-square="' + lastHighlightFrom + '"]');
      if (prev) prev.classList.remove('square-highlight');
    }
    if (lastHighlightTo && boardEl) {
      var prev2 = boardEl.querySelector('[data-square="' + lastHighlightTo + '"]');
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

  function setBoardFen(fen, oldFen) {
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
    var container = el('review-board');
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
    var ranksEl = el('review-board-ranks');
    if (ranksEl) {
      ranksEl.innerHTML = '';
      for (var r = 8; r >= 1; r--) {
        var span = document.createElement('span');
        span.textContent = r;
        ranksEl.appendChild(span);
      }
    }
    var filesEl = el('review-board-files');
    if (filesEl) {
      filesEl.innerHTML = '';
      for (var i = 0; i < 8; i++) {
        var span = document.createElement('span');
        span.textContent = files[i];
        filesEl.appendChild(span);
      }
    }
    updateBoardDom(defaultFen);
  }

  function parseChatTxt(txt) {
    var entries = [];
    var lines = (txt || '').split(/\n/);
    var i = 0;
    var headerRe = /^(White|Black)\s*\(([^)]*)\):\s*(.+)$/;
    while (i < lines.length) {
      var line = lines[i];
      var m = line.match(headerRe);
      if (m) {
        var side = m[1];
        var llmName = m[2].trim();
        var move = m[3].trim();
        var explanation = [];
        i++;
        while (i < lines.length && lines[i].trim() !== '') {
          explanation.push(lines[i]);
          i++;
        }
        entries.push({
          side: side,
          llmName: llmName,
          move: move,
          explanation: explanation.join('\n').trim()
        });
      }
      i++;
    }
    return entries;
  }

  function parsePgn(pgnText) {
    var game = new Chess();
    var loaded = game.load_pgn(pgnText);
    if (!loaded) return null;
    var history = game.history();
    var temp = new Chess();
    var fens = [temp.fen()];
    for (var i = 0; i < history.length; i++) {
      temp.move(history[i]);
      fens.push(temp.fen());
    }
    return { fens: fens, moves: history };
  }

  function renderMoveHistoryGrid(moves, currentMoveIdx) {
    if (!moves || moves.length === 0) return null;
    var grid = document.createElement('div');
    grid.className = 'move-history-grid';
    for (var i = 0; i < moves.length; i += 2) {
      var num = (i / 2) + 1;
      var w = moves[i] || '';
      var b = moves[i + 1] || '';
      var wMoveIdx = i + 1;
      var bMoveIdx = i + 2;

      var numEl = document.createElement('span');
      numEl.className = 'move-num';
      numEl.textContent = num + '.';
      grid.appendChild(numEl);

      var wEl = document.createElement('span');
      wEl.className = 'move-cell' + (wMoveIdx === currentMoveIdx ? ' move-cell-latest' : '');
      wEl.textContent = w;
      wEl.setAttribute('data-move-idx', wMoveIdx);
      grid.appendChild(wEl);

      var bEl = document.createElement('span');
      bEl.className = 'move-cell' + (bMoveIdx === currentMoveIdx ? ' move-cell-latest' : '');
      bEl.textContent = b;
      if (b) bEl.setAttribute('data-move-idx', bMoveIdx);
      grid.appendChild(bEl);
    }
    return grid;
  }

  function escapeHtml(s) {
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  function showError(msg) {
    var err = el('review-upload-error');
    if (err) { err.textContent = msg || ''; err.style.color = msg ? 'var(--danger)' : ''; }
  }

  function renderChatAll() {
    var chatEl = el('review-chat');
    if (!chatEl) return;
    if (moveLog.length === 0) {
      chatEl.innerHTML = '<p class="chat-empty">No comments in the chat log.</p>';
      return;
    }
    var html = '';
    for (var i = 0; i < moveLog.length; i++) {
      var entry = moveLog[i];
      var moveNum = i + 1;
      var sideClass = entry.side === 'White' ? 'chat-entry-white' : 'chat-entry-black';
      var label = entry.side + (entry.llmName ? ' (' + escapeHtml(entry.llmName) + ')' : '') + ': ';
      html += '<div class="chat-entry ' + sideClass + ' review-chat-entry' + (currentIndex === moveNum ? ' review-chat-entry-highlight' : '') + '" data-move-index="' + moveNum + '">';
      html += '<div class="chat-entry-header">' + label + '<strong>' + escapeHtml(entry.move || '') + '</strong></div>';
      if (entry.explanation) html += '<div class="chat-entry-explanation">' + escapeHtml(entry.explanation) + '</div>';
      html += '</div>';
    }
    chatEl.innerHTML = html;
  }

  function updateChatHighlightAndScroll() {
    var chatEl = el('review-chat');
    if (!chatEl) return;
    var entries = chatEl.querySelectorAll('.review-chat-entry');
    for (var i = 0; i < entries.length; i++) {
      var moveIdx = parseInt(entries[i].getAttribute('data-move-index'), 10);
      if (moveIdx === currentIndex) {
        entries[i].classList.add('review-chat-entry-highlight');
        entries[i].scrollIntoView({ block: 'start', behavior: 'smooth' });
      } else {
        entries[i].classList.remove('review-chat-entry-highlight');
      }
    }
  }

  function renderCurrentState() {
    if (fens.length === 0) return;
    var idx = Math.max(0, Math.min(currentIndex, fens.length - 1));
    var prevIdx = lastRenderedIndex;
    currentIndex = idx;

    if (prevIdx >= 0 && prevIdx < fens.length && prevIdx !== idx) {
      setBoardFen(fens[idx], fens[prevIdx]);
    } else {
      updateBoardDom(fens[idx]);
    }
    lastRenderedIndex = idx;

    var statusEl = el('review-status');
    var historyEl = el('review-move-history');
    var boardStatusEl = el('review-board-status');
    if (statusEl) statusEl.textContent = idx === 0 ? 'Initial position' : 'After move ' + idx;
    if (historyEl) {
      var grid = renderMoveHistoryGrid(moveHistory, idx);
      historyEl.innerHTML = '';
      if (grid) {
        historyEl.appendChild(grid);
        grid.addEventListener('click', function (e) {
          var cell = e.target.closest('.move-cell');
          if (!cell) return;
          var mi = parseInt(cell.getAttribute('data-move-idx'), 10);
          if (!isNaN(mi) && mi >= 0 && mi < fens.length) {
            currentIndex = mi;
            renderCurrentState();
          }
        });
        var latest = grid.querySelector('.move-cell-latest');
        if (latest) latest.scrollIntoView({ block: 'nearest' });
      }
    }
    if (boardStatusEl) boardStatusEl.textContent = idx === 0 ? 'Start' : 'Move ' + idx + ' of ' + moveHistory.length;

    var chatEl = el('review-chat');
    if (chatEl) {
      if (moveLog.length === 0) {
        chatEl.innerHTML = '<p class="chat-empty">No comments in the chat log.</p>';
      } else {
        if (chatEl.querySelectorAll('.review-chat-entry').length !== moveLog.length) {
          renderChatAll();
        }
        updateChatHighlightAndScroll();
      }
    }

    var ind = el('review-move-indicator');
    if (ind) ind.textContent = 'Move ' + idx + ' / ' + (fens.length - 1);

    var btnPrev = el('btn-prev');
    var btnNext = el('btn-next');
    if (btnPrev) btnPrev.disabled = idx <= 0;
    if (btnNext) btnNext.disabled = idx >= fens.length - 1;
  }

  function loadAndReview() {
    var pgnFile = el('file-pgn');
    var txtFile = el('file-txt');
    if (!pgnFile || !pgnFile.files || !pgnFile.files[0]) {
      showError('Please select a PGN file.');
      return;
    }
    if (!txtFile || !txtFile.files || !txtFile.files[0]) {
      showError('Please select a chat log (.txt) file.');
      return;
    }

    var pgnReader = new FileReader();
    var txtReader = new FileReader();
    var pgnDone = false;
    var txtDone = false;
    var pgnText = null;
    var txtText = null;

    function tryBuild() {
      if (!pgnDone || !txtDone) return;
      showError('');
      var parsed = parsePgn(pgnText);
      if (!parsed) {
        showError('Could not parse the PGN file.');
        return;
      }
      fens = parsed.fens;
      moveHistory = parsed.moves;
      moveLog = parseChatTxt(txtText);

      el('review-upload').style.display = 'none';
      el('review-main').style.display = 'flex';
      var header = el('review-header');
      if (header) {
        header.style.display = 'flex';
        header.innerHTML = '<span class="player white-player">Review</span><span class="vs">&middot;</span><span class="player black-player">' + moveHistory.length + ' moves</span>';
      }
      sizeBoardToViewport();
      renderBoard();
      currentIndex = 0;
      renderCurrentState();
    }

    pgnReader.onload = function () {
      pgnText = pgnReader.result;
      pgnDone = true;
      tryBuild();
    };
    txtReader.onload = function () {
      txtText = txtReader.result;
      txtDone = true;
      tryBuild();
    };
    pgnReader.readAsText(pgnFile.files[0]);
    txtReader.readAsText(txtFile.files[0]);
  }

  function goPrev() {
    if (fens.length === 0 || currentIndex <= 0) return;
    currentIndex--;
    renderCurrentState();
  }

  function goNext() {
    if (fens.length === 0 || currentIndex >= fens.length - 1) return;
    currentIndex++;
    renderCurrentState();
  }

  function sizeBoardToViewport() {
    var header = document.querySelector('.header');
    var headerH = header ? header.offsetHeight : 80;
    var available = window.innerHeight - headerH - 32;
    var boardSize = Math.min(Math.max(Math.floor(available - 40), 320), 680);
    document.documentElement.style.setProperty('--board-size', boardSize + 'px');
  }

  function init() {
    sizeBoardToViewport();
    window.addEventListener('resize', sizeBoardToViewport);
    renderBoard();

    el('btn-load-review').addEventListener('click', loadAndReview);
    el('btn-prev').addEventListener('click', goPrev);
    el('btn-next').addEventListener('click', goNext);

    document.addEventListener('keydown', function (e) {
      if (fens.length === 0) return;
      var tag = (e.target && e.target.tagName) ? e.target.tagName.toUpperCase() : '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || (e.target && e.target.isContentEditable)) return;
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        goPrev();
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        goNext();
      }
    });
  }

  init();
})();
