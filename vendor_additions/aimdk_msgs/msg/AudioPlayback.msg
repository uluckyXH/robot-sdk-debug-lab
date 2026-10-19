# ==========================================
# [话题载荷 Payload] 音频流回放结构体
# ==========================================
builtin_interfaces/Time  stamps          # 时间戳
AudioInfo                info            # 音频格式
AudioData                data            # 音频数据
string                   pkg_name        # 标识发送方来源
string                   token_id        # 可选项，用于清理播放缓存（打断播放）
