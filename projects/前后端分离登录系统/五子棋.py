import pygame
import sys

# 初始化 Pygame
pygame.init()

# 颜色定义
BACKGROUND = (220, 179, 92)  # 棋盘背景色 (米黄色)
LINE_COLOR = (0, 0, 0)       # 棋盘线颜色 (黑色)
BLACK = (0, 0, 0)            # 黑棋
WHITE = (255, 255, 255)      # 白棋
RED = (255, 0, 0)            # 红色 (用于提示)
TEXT_COLOR = (50, 50, 50)    # 文字颜色

class Gomoku:
    def __init__(self, board_size=15):
        self.board_size = board_size
        self.board = [['.' for _ in range(board_size)] for _ in range(board_size)]
        self.current_player = 'B'  # B: 黑棋, W: 白棋
        self.game_over = False
        self.winner = None
        
        # Pygame 设置
        self.cell_size = 40  # 每个格子的像素大小
        self.margin = 50     # 边距
        self.radius = 18     # 棋子半径
        
        # 计算窗口大小
        self.width = 2 * self.margin + (board_size - 1) * self.cell_size
        self.height = 2 * self.margin + (board_size - 1) * self.cell_size + 100  # 额外空间显示信息
        
        # 创建窗口
        self.screen = pygame.display.set_mode((self.width, self.height))
        pygame.display.set_caption("五子棋")
        
        # 字体
        self.font = pygame.font.SysFont(None, 32)
        self.small_font = pygame.font.SysFont(None, 24)
        
    def draw_board(self):
        """绘制棋盘"""
        # 填充背景色
        self.screen.fill(BACKGROUND)
        
        # 绘制棋盘线
        for i in range(self.board_size):
            # 横线
            start_pos = (self.margin, self.margin + i * self.cell_size)
            end_pos = (self.width - self.margin, self.margin + i * self.cell_size)
            pygame.draw.line(self.screen, LINE_COLOR, start_pos, end_pos, 2)
            
            # 竖线
            start_pos = (self.margin + i * self.cell_size, self.margin)
            end_pos = (self.margin + i * self.cell_size, self.height - self.margin - 100)
            pygame.draw.line(self.screen, LINE_COLOR, start_pos, end_pos, 2)
        
        # 绘制棋盘上的点 (天元和星)
        points = [(3, 3), (3, 11), (7, 7), (11, 3), (11, 11)]
        for row, col in points:
            x = self.margin + col * self.cell_size
            y = self.margin + row * self.cell_size
            pygame.draw.circle(self.screen, LINE_COLOR, (x, y), 5)
        
    def draw_pieces(self):
        """绘制棋子"""
        for row in range(self.board_size):
            for col in range(self.board_size):
                if self.board[row][col] != '.':
                    x = self.margin + col * self.cell_size
                    y = self.margin + row * self.cell_size
                    
                    color = BLACK if self.board[row][col] == 'B' else WHITE
                    pygame.draw.circle(self.screen, color, (x, y), self.radius)
                    
                    # 为白棋添加黑色边框
                    if self.board[row][col] == 'W':
                        pygame.draw.circle(self.screen, BLACK, (x, y), self.radius, 1)
    
    def draw_info(self):
        """绘制游戏信息"""
        # 当前玩家信息
        player_text = "当前玩家: 黑棋" if self.current_player == 'B' else "当前玩家: 白棋"
        player_color = BLACK if self.current_player == 'B' else WHITE
        
        # 绘制当前玩家指示器
        indicator_x = self.width // 2 - 100
        indicator_y = self.height - 80
        pygame.draw.circle(self.screen, player_color, (indicator_x, indicator_y), 15)
        if self.current_player == 'W':
            pygame.draw.circle(self.screen, BLACK, (indicator_x, indicator_y), 15, 1)
        
        # 绘制文字
        text_surface = self.font.render(player_text, True, TEXT_COLOR)
        self.screen.blit(text_surface, (indicator_x + 30, indicator_y - 15))
        
        # 游戏状态
        if self.game_over:
            winner_text = "黑棋获胜!" if self.winner == 'B' else "白棋获胜!"
            text_surface = self.font.render(winner_text, True, RED)
            self.screen.blit(text_surface, (self.width // 2 - 60, self.height - 40))
        else:
            # 操作提示
            hint_text = "点击棋盘落子 | R: 重新开始 | ESC: 退出"
            text_surface = self.small_font.render(hint_text, True, TEXT_COLOR)
            self.screen.blit(text_surface, (self.width // 2 - 120, self.height - 40))
    
    def get_board_position(self, mouse_pos):
        """将鼠标位置转换为棋盘坐标"""
        x, y = mouse_pos
        
        # 检查是否在棋盘范围内
        if (x < self.margin - self.cell_size // 2 or 
            x > self.width - self.margin + self.cell_size // 2 or
            y < self.margin - self.cell_size // 2 or 
            y > self.height - self.margin - 100 + self.cell_size // 2):
            return None, None
        
        # 计算最近的交叉点
        col = round((x - self.margin) / self.cell_size)
        row = round((y - self.margin) / self.cell_size)
        
        # 确保在棋盘范围内
        if 0 <= row < self.board_size and 0 <= col < self.board_size:
            return row, col
        return None, None
    
    def make_move(self, row, col):
        """落子"""
        if not (0 <= row < self.board_size and 0 <= col < self.board_size):
            return False
            
        if self.board[row][col] != '.':
            return False
            
        self.board[row][col] = self.current_player
        
        # 检查是否获胜
        if self.check_win(row, col):
            self.game_over = True
            self.winner = self.current_player
            return True
            
        # 切换玩家
        self.current_player = 'W' if self.current_player == 'B' else 'B'
        return True
        
    def check_win(self, row, col):
        """检查是否获胜"""
        player = self.board[row][col]
        
        # 检查的八个方向：水平、垂直、两个对角线
        directions = [
            (0, 1),   # 水平向右
            (1, 0),   # 垂直向下
            (1, 1),   # 右下对角线
            (1, -1)   # 左下对角线
        ]
        
        for dr, dc in directions:
            count = 1  # 当前位置已经有一个棋子
            
            # 正向检查
            r, c = row + dr, col + dc
            while 0 <= r < self.board_size and 0 <= c < self.board_size and self.board[r][c] == player:
                count += 1
                r += dr
                c += dc
                
            # 反向检查
            r, c = row - dr, col - dc
            while 0 <= r < self.board_size and 0 <= c < self.board_size and self.board[r][c] == player:
                count += 1
                r -= dr
                c -= dc
                
            if count >= 5:
                return True
                
        return False
    
    def reset_game(self):
        """重置游戏"""
        self.board = [['.' for _ in range(self.board_size)] for _ in range(self.board_size)]
        self.current_player = 'B'
        self.game_over = False
        self.winner = None
        
    def run(self):
        """运行游戏主循环"""
        clock = pygame.time.Clock()
        
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        pygame.quit()
                        sys.exit()
                    elif event.key == pygame.K_r:
                        self.reset_game()
                
                if event.type == pygame.MOUSEBUTTONDOWN and not self.game_over:
                    if event.button == 1:  # 左键点击
                        row, col = self.get_board_position(event.pos)
                        if row is not None and col is not None:
                            self.make_move(row, col)
            
            # 绘制游戏
            self.draw_board()
            self.draw_pieces()
            self.draw_info()
            
            # 更新显示
            pygame.display.flip()
            clock.tick(60)

if __name__ == "__main__":
    game = Gomoku()
    game.run()
