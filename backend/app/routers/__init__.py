"""业务路由汇总：空组出数证据等固定路径优先注册，再挂通用模块资源路由。"""
from __future__ import annotations

from app.income import router as income_router
from app.routers import resource

ROUTERS = [income_router, resource]
