import pygame
import sys
import numpy as np

# 初始化pygame
pygame.init()

# 颜色定义
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
BOARD_COLOR = (220, 179, 92)  # 棋盘颜色
GRID_COLOR = (0, 0, 0)        # 网格线颜色
TEXT_COLOR = (50, 50, 50)

class Gomoku:
    def __init__(self, board_size=15, cell_size=40):
        self.board_size = board_size
        self.cell_size = cell_size
        self.board = np.zeros((board_size, board_size), dtype=int)
        self.current_player = 1  # 玩家1为黑棋，玩家2为白棋
        self.game_over = False
        self.winner = None
        
        # 计算窗口大小
        self.margin = 50  # 边距
        self.board_width = board_size * cell_size
        self.board_height = board_size * cell_size
        self.window_width = self.board_width + 2 * self.margin
        self.window_height = self.board_height + 2 * self.margin + 100  # 额外空间显示信息
        
        # 创建窗口
        self.screen = pygame.display.set_mode((self.window_width, self.window_height))
        pygame.display.set_caption("五子棋游戏")
        
        # 加载字体
        self.font = pygame.font.SysFont(None, 28)
        self.title_font = pygame.font.SysFont(None, 36)
        
        # 棋子半径
        self.stone_radius = cell_size // 2 - 2
        
    def draw_board(self):
        """绘制棋盘"""
        # 填充背景色
        self.screen.fill((240, 240, 240))
        
        # 绘制棋盘区域
        board_rect = pygame.Rect(
            self.margin, 
            self.margin, 
            self.board_width, 
            self.board_height
        )
        pygame.draw.rect(self.screen, BOARD_COLOR, board_rect)
        
        # 绘制网格线
        for i in range(self.board_size + 1):
            # 水平线
            pygame.draw.line(
                self.screen, 
                GRID_COLOR, 
                (self.margin, self.margin + i * self.cell_size),
                (self.margin + self.board_width, self.margin + i * self.cell_size),
                2
            )
            # 垂直线
            pygame.draw.line(
                self.screen, 
                GRID_COLOR, 
                (self.margin + i * self.cell_size, self.margin),
                (self.margin + i * self.cell_size, self.margin + self.board_height),
                2
            )
        
        # 绘制棋盘上的点（天元和星）
        points = [(3, 3), (3, 11), (7, 7), (11, 3), (11, 11)]
        for point in points:
            x = self.margin + point[0] * self.cell_size
            y = self.margin + point[1] * self.cell_size
            pygame.draw.circle(self.screen, GRID_COLOR, (x, y), 5)
    
    def draw_stones(self):
        """绘制棋子"""
        for i in range(self.board_size):
            for j in range(self.board_size):
                if self.board[i][j] != 0:
                    x = self.margin + j * self.cell_size
                    y = self.margin + i * self.cell_size
                    
                    if self.board[i][j] == 1:  # 黑棋
                        pygame.draw.circle(self.screen, BLACK, (x, y), self.stone_radius)
                        # 添加一点高光效果
                        pygame.draw.circle(self.screen, (100, 100, 100), (x-3, y-3), 3)
                    else:  # 白棋
                        pygame.draw.circle(self.screen, WHITE, (x, y), self.stone_radius)
                        pygame.draw.circle(self.screen, BLACK, (x, y), self.stone_radius, 1)
    
    def draw_info(self):
        """绘制游戏信息"""
        info_y = self.margin + self.board_height + 20
        
        # 绘制标题
        title = self.title_font.render("五子棋游戏", True, TEXT_COLOR)
        self.screen.blit(title, (self.window_width // 2 - title.get_width() // 2, info_y))
        
        # 绘制当前玩家信息
        info_y += 40
        if not self.game_over:
            current_color = "黑棋(●)" if self.current_player == 1 else "白棋(○)"
            player_text = f"当前玩家: 玩家{self.current_player} {current_color}"
        elif self.winner:
            winner_color = "黑棋(●)" if self.winner == 1 else "白棋(○)"
            player_text = f"玩家{self.winner} {winner_color} 获胜！"
        else:
            player_text = "平局！"
        
        player_surface = self.font.render(player_text, True, TEXT_COLOR)
        self.screen.blit(player_surface, (self.window_width // 2 - player_surface.get_width() // 2, info_y))
        
        # 绘制操作提示
        info_y += 30
        hint_text = "点击棋盘落子 | R: 重新开始 | ESC: 退出游戏"
        hint_surface = self.font.render(hint_text, True, TEXT_COLOR)
        self.screen.blit(hint_surface, (self.window_width // 2 - hint_surface.get_width() // 2, info_y))
    
    def make_move(self, row, col):
        """落子"""
        if self.game_over:
            return False
            
        if row < 0 or row >= self.board_size or col < 0 or col >= self.board_size:
            return False
            
        if self.board[row][col] != 0:
            return False
            
        self.board[row][col] = self.current_player
        
        # 检查是否获胜
        if self.check_win(row, col):
            self.game_over = True
            self.winner = self.current_player
            return True
            
        # 检查平局
        if self.check_draw():
            self.game_over = True
            return True
            
        # 切换玩家
        self.current_player = 3 - self.current_player  # 1->2, 2->1
        return True
    
    def check_win(self, row, col):
        """检查是否五子连珠"""
        player = self.board[row][col]
        
        # 检查的八个方向
        directions = [
            (0, 1),   # 水平
            (1, 0),   # 垂直
            (1, 1),   # 主对角线
            (1, -1)   # 副对角线
        ]
        
        for dr, dc in directions:
            count = 1  # 当前位置已有一个棋子
            
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
    
    def check_draw(self):
        """检查是否平局（棋盘已满）"""
        return np.all(self.board != 0)
    
    def reset_game(self):
        """重置游戏"""
        self.board = np.zeros((self.board_size, self.board_size), dtype=int)
        self.current_player = 1
        self.game_over = False
        self.winner = None
    
    def get_board_position(self, mouse_x, mouse_y):
        """将鼠标坐标转换为棋盘位置"""
        # 检查是否在棋盘范围内
        if (mouse_x < self.margin or mouse_x >= self.margin + self.board_width or
            mouse_y < self.margin or mouse_y >= self.margin + self.board_height):
            return None, None
        
        # 计算行列
        col = (mouse_x - self.margin) // self.cell_size
        row = (mouse_y - self.margin) // self.cell_size
        
        return row, col
    
    def run(self):
        """运行游戏主循环"""
        clock = pygame.time.Clock()
        
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        pygame.quit()
                        sys.exit()
                    elif event.key == pygame.K_r:
                        self.reset_game()
                
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1:  # 左键点击
                        mouse_x, mouse_y = pygame.mouse.get_pos()
                        row, col = self.get_board_position(mouse_x, mouse_y)
                        
                        if row is not None and col is not None:
                            self.make_move(row, col)
            
            # 绘制游戏
            self.draw_board()
            self.draw_stones()
            self.draw_info()
            
            # 更新显示
            pygame.display.flip()
            clock.tick(60)

def main():
    """主函数"""
    game = Gomoku()
    game.run()

if __name__ == "__main__":
    main()
