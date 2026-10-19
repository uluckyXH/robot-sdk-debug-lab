# ==========================================
# [话题载荷 Payload] 音频流采集结构体
# ==========================================
builtin_interfaces/Time  stamps          # 时间戳
uint8                    mic_channels    # 麦克风通道数
uint8                    ref_channels    # 回采信号通道数
AudioInfo                info            # 音频格式
AudioData                data            # 音频数据
string                   pkg_name        # 标识发送方来源
uint32                   mic_source      # 0-内置麦 1-外置麦
