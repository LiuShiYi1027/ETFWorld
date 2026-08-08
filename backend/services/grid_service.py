"""
网格策略规划器

E大网格策略：
- 网格1.0：从基准价开始，每跌一格(grid_step%)买入一份，回升一格卖出对应份额
- 网格2.0：在1.0基础上支持"留利润"——卖出时保留部分利润份额，长期积累底仓
- 逐格加码：越跌买入金额越大(step_increase%)，摊低成本

价格几何对称：买入价按 (1-step)^i 逐格下移，卖出价 = 买入价/(1-step)，
即严格"回升一格"，与上一格买入价重合。
"""
import logging
from typing import Dict, List, Optional

from backend.models.database import GridPlanTable, TradeTable
from backend.utils.db import get_session

logger = logging.getLogger(__name__)


def generate_levels(
    base_price: float,
    grid_step: float,
    grid_count: int,
    amount_per_grid: float = 0,
    step_increase: float = 0,
    profit_retention: float = 0,
    grid_mode: str = 'amount',
    shares_per_grid: float = 0,
) -> List[Dict]:
    """
    生成网格档位表

    Args:
        base_price: 基准价（第1格买入价）
        grid_step: 网格大小（%），如5表示每格5%
        grid_count: 网格数量
        amount_per_grid: 第1格买入金额（grid_mode=amount 时生效）
        step_increase: 逐格加码比例（%），amount 模式加码金额、shares 模式加码份额
        profit_retention: 留利润比例（%），卖出时保留利润对应份额（网格2.0）
        grid_mode: 投入方式 amount=等金额（每格金额恒定，份额随下跌增多）/
                   shares=等份额（每格份额恒定，金额随下跌减少，天然限制底部投入）
        shares_per_grid: 第1格买入份额（grid_mode=shares 时生效，按100股整手取整）

    Returns:
        档位列表，每档含买入价、卖出价、金额、份额、预期收益
    """
    step = grid_step / 100
    levels = []
    for i in range(grid_count):
        # 第i格买入价：基准价按几何方式逐格下移
        buy_price = round(base_price * (1 - step) ** i, 4)
        # 卖出价 = 买入价/(1-step)，严格"回升一格"，与上一格买入价重合
        sell_price = round(buy_price / (1 - step), 4)
        if grid_mode == 'shares':
            # 等份额：份额逐格恒定（逐格加码时按份额递增），金额随价格下跌而减少
            shares = int(shares_per_grid * (1 + step_increase / 100) ** i / 100) * 100
        else:
            # 等金额：金额逐格恒定（逐格加码时按金额递增），份额随价格下跌而增多
            amount = round(amount_per_grid * (1 + step_increase / 100) ** i, 2)
            shares = int(amount / buy_price / 100) * 100 if buy_price > 0 else 0
        actual_amount = round(shares * buy_price, 2)

        gross_profit = round(shares * (sell_price - buy_price), 2)
        retained_profit = round(gross_profit * profit_retention / 100, 2)
        # A股以100股(1手)为交易单位：卖出向下取整到整手，不足整手的漂移全部进
        # 留存，因此卖出/留存都是100的整数倍。向下取整保证只要设了留利润、每格
        # 至少留一手底仓（四舍五入会让小网格留存归零，违背留利润初衷）。
        # 留利润为0时 retained_target=0，退化为卖出全部份额（网格1.0）。
        retained_target = (retained_profit / sell_price) if sell_price > 0 else 0
        sell_shares = int((shares - retained_target) / 100) * 100
        sell_shares = max(0, min(shares, sell_shares))
        retained_shares = shares - sell_shares
        realized_profit = round(sell_shares * sell_price - actual_amount, 2)

        levels.append({
            'level': i + 1,
            'fall_pct': round((1 - buy_price / base_price) * 100, 2),
            'buy_price': buy_price,
            'sell_price': sell_price,
            'amount': actual_amount,
            'shares': shares,
            'sell_shares': sell_shares,
            'retained_shares': retained_shares,
            'expected_profit': realized_profit if profit_retention > 0 else gross_profit,
            'profit_rate': round((sell_price - buy_price) / buy_price * 100, 2),
        })
    return levels


def match_grid_level(levels: List[Dict], direction: str, price: float,
                     tolerance: float = 0.015) -> Optional[int]:
    """
    成交自动匹配档位：买入对 buy_price、卖出对 sell_price 找价格最近的档位，
    容差 ±tolerance（默认 1.5%，覆盖滑点与手动挂单价差）。匹配不到返回 None。
    """
    if not levels or not price or price <= 0:
        return None
    key = 'buy_price' if direction == 'buy' else 'sell_price'
    best_level, best_dist = None, None
    for l in levels:
        target = float(l.get(key) or 0)
        if target <= 0:
            continue
        dist = abs(price - target) / target
        if best_dist is None or dist < best_dist:
            best_level, best_dist = l.get('level'), dist
    return best_level if best_dist is not None and best_dist <= tolerance else None


def derive_level_states(levels: List[Dict], trades) -> List[str]:
    """
    由成交记录推导每档执行状态（棋盘四态）：
    - wait  待买：无任何成交
    - hold  持有：有买入、还没卖出
    - sold  已卖：买卖相抵（网格1.0 的完成态）
    - keep  留存：卖出后仍有净剩余份额（网格2.0 留利润的免费底仓）

    trades 为 TradeTable ORM 行（只需 direction / shares / grid_level 三个字段）。
    """
    bought: Dict[int, float] = {}
    sold: Dict[int, float] = {}
    for t in trades:
        if t.grid_level is None:
            continue
        bucket = bought if t.direction == 'buy' else sold
        bucket[t.grid_level] = bucket.get(t.grid_level, 0.0) + float(t.shares)
    states = []
    for l in levels:
        lv = l.get('level')
        b, s = bought.get(lv, 0.0), sold.get(lv, 0.0)
        if b <= 0:
            states.append('wait')
        elif s <= 0:
            states.append('hold')
        elif b - s > 1e-6:
            states.append('keep')
        else:
            states.append('sold')
    return states


def pressure_test(levels: List[Dict], base_price: float) -> Dict:
    """
    压力测试：若全部网格被触发（跌到最深一格），需要多少资金、浮亏多少
    """
    if not levels:
        return {}
    total_capital = sum(l['amount'] for l in levels)
    total_shares = sum(l['shares'] for l in levels)
    lowest_price = levels[-1]['buy_price']
    max_fall = levels[-1]['fall_pct']
    # 全部成交后，按最低价计算的持仓市值与浮亏
    market_value = round(total_shares * lowest_price, 2)
    avg_cost = round(total_capital / total_shares, 4) if total_shares else 0
    unrealized_loss = round(market_value - total_capital, 2)
    return {
        'total_capital': round(total_capital, 2),
        'total_shares': total_shares,
        'max_fall_pct': max_fall,
        'lowest_price': lowest_price,
        'avg_cost': avg_cost,
        'market_value_at_bottom': market_value,
        'max_unrealized_loss': unrealized_loss,
        'max_unrealized_loss_pct': round(unrealized_loss / total_capital * 100, 2)
        if total_capital else 0,
    }


def _resolve_mode(params: Dict) -> tuple:
    """解析投入方式：返回 (grid_mode, amount_per_grid, shares_per_grid)。

    amount 模式要求每格金额 > 0；shares 模式要求每格份额 ≥ 100（1 手）。
    shares 模式下 amount_per_grid 归一为 0（档位金额以 levels 为准）。
    """
    mode = params.get('grid_mode') or 'amount'
    if mode not in ('amount', 'shares'):
        raise ValueError("grid_mode 必须是 amount(等金额) 或 shares(等份额)")
    if mode == 'shares':
        shares = float(params.get('shares_per_grid') or 0)
        if shares < 100:
            raise ValueError('等份额模式下每格份额需 ≥ 100（1 手）')
        return mode, 0.0, shares
    amount = float(params.get('amount_per_grid') or 0)
    if amount <= 0:
        raise ValueError('每格金额必须大于 0')
    return mode, amount, 0.0


class GridService:
    """网格计划管理"""

    def preview(self, params: Dict) -> Dict:
        """预览网格计划（不保存）"""
        mode, amount, shares = _resolve_mode(params)
        levels = generate_levels(
            base_price=float(params['base_price']),
            grid_step=float(params['grid_step']),
            grid_count=int(params['grid_count']),
            amount_per_grid=amount,
            step_increase=float(params.get('step_increase', 0)),
            profit_retention=float(params.get('profit_retention', 0)),
            grid_mode=mode,
            shares_per_grid=shares,
        )
        return {
            'levels': levels,
            'pressure_test': pressure_test(levels, float(params['base_price'])),
        }

    def create_plan(self, params: Dict) -> Dict:
        """创建并保存网格计划"""
        mode, amount, shares = _resolve_mode(params)
        result = self.preview(params)
        with get_session() as session:
            plan = GridPlanTable(
                name=params['name'],
                symbol=params['symbol'],
                symbol_name=params.get('symbol_name'),
                version='2.0' if float(params.get('profit_retention', 0)) > 0 else '1.0',
                grid_mode=mode,
                shares_per_grid=shares or None,
                base_price=params['base_price'],
                grid_step=params['grid_step'],
                grid_count=params['grid_count'],
                amount_per_grid=amount,
                step_increase=params.get('step_increase', 0),
                profit_retention=params.get('profit_retention', 0),
                levels=result['levels'],
                note=params.get('note'),
            )
            session.add(plan)
            session.flush()
            plan_id = plan.id
        return {'id': plan_id, **result}

    def list_plans(self) -> List[Dict]:
        with get_session() as session:
            plans = session.query(GridPlanTable) \
                .order_by(GridPlanTable.created_at.desc()).all()
            return [self._to_dict(p) for p in plans]

    def get_plan(self, plan_id: int) -> Optional[Dict]:
        with get_session() as session:
            plan = session.get(GridPlanTable, plan_id)
            if not plan:
                return None
            d = self._to_dict(plan)
            d['pressure_test'] = pressure_test(plan.levels or [], float(plan.base_price))
            trades = session.query(TradeTable).filter_by(plan_id=plan_id).all()
            d['level_states'] = derive_level_states(plan.levels or [], trades)
            return d

    def break_action(self, plan_id: int, action: str,
                     new_base_price: float = None) -> Optional[Dict]:
        """
        破网处置三选一：
        - hold   装死持有：标记 broken，不再挂新单，等价格回到网内
        - extend 向下接网：以 new_base_price（现价）为基准按原参数生成新计划，旧计划标记 broken
        - stop   止损归档：标记 closed
        """
        if action not in ('hold', 'extend', 'stop'):
            raise ValueError("action 必须是 hold / extend / stop")
        with get_session() as session:
            plan = session.get(GridPlanTable, plan_id)
            if not plan:
                return None
            params = {  # 快照原参数（extend 要用；session 关闭后 ORM 字段可能不可用）
                'name': f'{plan.name}·接网', 'symbol': plan.symbol,
                'symbol_name': plan.symbol_name,
                'base_price': new_base_price, 'grid_step': float(plan.grid_step),
                'grid_count': plan.grid_count,
                'amount_per_grid': float(plan.amount_per_grid),
                'grid_mode': plan.grid_mode or 'amount',
                'shares_per_grid': float(plan.shares_per_grid or 0),
                'step_increase': float(plan.step_increase or 0),
                'profit_retention': float(plan.profit_retention or 0),
            }
            if action == 'extend':
                if not new_base_price or new_base_price <= 0:
                    raise ValueError('向下接网需要有效的新基准价（现价）')
                plan.status = 'broken'
            elif action == 'hold':
                plan.status = 'broken'
            else:
                plan.status = 'closed'
        if action == 'extend':
            created = self.create_plan(params)
            return {'action': 'extend', 'id': plan_id, 'new_plan_id': created['id']}
        return {'action': action, 'id': plan_id}

    def update_status(self, plan_id: int, status: str) -> bool:
        with get_session() as session:
            plan = session.get(GridPlanTable, plan_id)
            if not plan:
                return False
            plan.status = status
            return True

    def delete_plan(self, plan_id: int) -> bool:
        with get_session() as session:
            plan = session.get(GridPlanTable, plan_id)
            if not plan:
                return False
            session.delete(plan)
            return True

    @staticmethod
    def _to_dict(p: GridPlanTable) -> Dict:
        return {
            'id': p.id,
            'name': p.name,
            'symbol': p.symbol,
            'symbol_name': p.symbol_name,
            'version': p.version,
            'grid_mode': p.grid_mode or 'amount',
            'shares_per_grid': float(p.shares_per_grid) if p.shares_per_grid else None,
            'base_price': float(p.base_price),
            'grid_step': float(p.grid_step),
            'grid_count': p.grid_count,
            'amount_per_grid': float(p.amount_per_grid),
            'step_increase': float(p.step_increase or 0),
            'profit_retention': float(p.profit_retention or 0),
            'levels': p.levels,
            'status': p.status,
            'note': p.note,
            'created_at': p.created_at.isoformat() if p.created_at else None,
        }
