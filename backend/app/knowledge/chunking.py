"""글 덩어리 나누기. 순수 함수.

- 표: 행을 자르지 않고 20행씩 묶는다. 덩어리마다 첫 줄에 머리글을 붙이고, 1,500자를 넘으면 행 수를 줄인다.
  한 행 혼자서 1,500자를 넘으면 그 행만 잘라 맞춘다.
- 일반 글: 줄(문장) 경계에서 자른다. 1,200자를 넘으면 닫고, 다음 덩어리는 직전 끝 100자와 겹쳐 시작한다.
"""
from dataclasses import dataclass

from app.knowledge.extract import Block

MAX_CHARS = 1500
TARGET_CHARS = 1200
OVERLAP = 100
TABLE_ROWS = 20
_PIECE = 1000  # 한 줄이 이보다 길면 이 길이로 쪼갠다


@dataclass
class ChunkData:
    text: str
    lines: list[str]


def _make(lines: list[str]) -> ChunkData:
    return ChunkData("\n".join(lines), lines)


def _table_chunks(block: Block) -> list[ChunkData]:
    header = block.heading[: MAX_CHARS // 2]
    budget = MAX_CHARS - len(header) - 1
    rows = [r if len(r) + 1 <= budget else r[: budget - 1] for r in block.lines]
    out: list[ChunkData] = []
    i = 0
    while i < len(rows):
        n = min(TABLE_ROWS, len(rows) - i)
        while n > 1 and len(header) + sum(len(r) + 1 for r in rows[i:i + n]) > MAX_CHARS:
            n -= 1
        out.append(_make(([header] if header else []) + rows[i:i + n]))
        i += n
    return out


def _text_chunks(block: Block) -> list[ChunkData]:
    pieces: list[str] = []
    for line in ([block.heading] if block.heading else []) + block.lines:
        pieces.extend(line[k:k + _PIECE] for k in range(0, len(line), _PIECE))
    out: list[ChunkData] = []
    cur: list[str] = []
    size = 0
    new = False  # 겹친 앞부분 말고 새 줄이 들어 있는지

    def close() -> None:
        nonlocal cur, size, new
        if new:
            chunk = _make(cur)
            out.append(chunk)
            tail = chunk.text[-OVERLAP:]
            cur, size = tail.split("\n"), len(tail)
        new = False

    for p in pieces:
        if new and (size >= TARGET_CHARS or size + len(p) + 1 > MAX_CHARS):
            close()
        cur.append(p)
        size += len(p) + 1
        new = True
    close()
    return out


def chunk_blocks(blocks: list[Block]) -> list[ChunkData]:
    out: list[ChunkData] = []
    for b in blocks:
        out.extend(_table_chunks(b) if b.kind == "table" else _text_chunks(b))
    return out
