"""路由器接口路径（集中管理，便于以后随固件调整）。"""

from __future__ import annotations

TOKEN_PATH = "/?_type=loginsceneData&_tag=login_token_json"
LOGIN_PATH = "/?_type=loginData&_tag=login_entry"
VERIFY_PATH = "/?_type=vuejsonData&_tag=aclmode_data"
LAN_INFO_PATH = "/?_type=vueData&_tag=localnet_lan_info_lua"
QOS_BY_MAC_PATH = "/?_type=vuejsonData&_tag=localnet_qosdyn_rulebymac"
QOS_RULE_PATH = "/?_type=vueData&_tag=localnet_qosdyn_bandwithrule_lua"
