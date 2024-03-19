import torch
import torch.nn.functional as F
from torch import nn, einsum

# torch >= 2.0.0
# from torch.backends.cuda import sdp_kernel

def zero_module(module):
    """
    Zero out the parameters of a module and return it.
    """
    for p in module.parameters():
        p.detach().zero_()
    return module


class GEGLU(nn.Module):
    """
    https://arxiv.org/abs/2002.05202v1
    """
    def __init__(self, dim_in, dim_out):
        super().__init__()
        self.proj = nn.Linear(dim_in, dim_out*2)

    def forward(self, x):
        x, gate = self.proj(x).chunk(2, dim=-1)
        return x * F.gelu(gate)


class Attention(nn.Module):
    """
    Attention Block for Self-Attention and Cross-Attention.
    :query_dim: Dimension of query
    :context_dim: Dimension of context, None parameter works as Self-Attention (default=None)
    :num_heads: Number of Head (default=4)
    :head_dim: Dimension of Head, None parameter define head_dim to query_dim (default=None)
    :dropout: DropOut Rate (default=0.)
    """
    def __init__(self,
                 query_dim: int,
                 context_dim: int = None,
                 num_heads: int = 4,
                 head_dim: int = 32,
                 dropout: float = 0.
                 ):
        super().__init__()
        assert query_dim 

        if not head_dim:
            head_dim = query_dim
        inner_dim = num_heads * head_dim
        # inner_dim = 128 #TODO: temp val

        self.cross = True if context_dim is not None else False
        if not self.cross:
            context_dim = query_dim
        
        self.scale = head_dim ** -0.5
        self.heads = num_heads
        self.head_dim = head_dim


        self.to_q = nn.Linear(query_dim, inner_dim, bias=False)
        self.to_k = nn.Linear(context_dim, inner_dim, bias=False)
        self.to_v = nn.Linear(context_dim, inner_dim, bias=False)

        self.to_out = nn.Sequential(
            nn.Linear(inner_dim, query_dim),
            nn.Dropout(p=dropout)
        )

    def forward(self, x, context=None):
        if context is not None and self.cross == False:
            raise ValueError("context should None if context_dim is None")

        h = self.heads
        d = self.head_dim
        b = x.size()[0]

        q = self.to_q(x)
        context = context if context is not None else x # if context is None, work self attn
        k = self.to_k(context)
        v = self.to_v(context)

        q, k, v = map(lambda t: t.view(b, -1, h, d).permute(0, 2, 1, 3), (q, k, v)) # b n (h d) -> b h n d

        # sim = einsum('b i d, b j d -> b i j' , q, k) * self.scale
        # attn = sim.softmax(dim=-1)
        
        # out = einsum('b i j, b j d -> b i d', attn, v)

        # with sdp_kernel(enable_flash=True, enable_math=False, enable_mem_efficient=False): # enable_mem_efficient 는 계산값 일관성이 떨어진다고 한다
        out = F.scaled_dot_product_attention(q, k, v)

        # out = out.permute(0, 2, 1, 3).contiguous().view(b, -1, h*d) # (b h) n d -> b n (h d)
        out = out.permute(0, 2, 1, 3).view(b, -1, h*d) # b h n d -> b n (h d)

        return self.to_out(out)


class BasicTransformerBlock(nn.Module):
    """
    Basic Trasformer Block include self-attention, cross-attention, feedforward network.
    :dim: Demension of input tensor
    :num_heads: Number of Attention Head 
    :head_dim: Dimension of Attention Head
    :context_dim: Dimension of context
    :ff_dim_mult: Multiply Value of Feed Forward Network Inner Dimension (default=4)
    :use_GEGLU: Whether to use GEGLU or GELU (default=True)
    :dropout: DropOut Rate (default=0.)
    """
    def __init__(self,
                 dim: int,
                 num_heads: int,
                 head_dim: int,
                 context_dim: int = None,
                 ff_dim_mult: int = 4,
                 use_GEGLU: bool = True,
                 dropout: float = 0.,
                 ):
        super().__init__()
        ff_inner_dim = int(dim * ff_dim_mult)
        ff_layer = [nn.Linear(dim, ff_inner_dim), nn.GELU()] if not use_GEGLU else [GEGLU(dim, ff_inner_dim)]

        self.attn1 = Attention(query_dim=dim, num_heads=num_heads, head_dim=head_dim, dropout=dropout) # self-attention
        self.attn2 = Attention(query_dim=dim, context_dim=context_dim, num_heads=num_heads, head_dim=head_dim, dropout=dropout) # cross-attention

        self.ff = nn.Sequential(
            *ff_layer,
            nn.Dropout(p=dropout),
            nn.Linear(ff_inner_dim, dim)
        )

        self.norm1 = nn.LayerNorm(dim)
        self.norm2 = nn.LayerNorm(dim)
        self.norm3 = nn.LayerNorm(dim)

    def forward(self, x, context=None):
        x = self.attn1(self.norm1(x)) + x
        x = self.attn2(self.norm2(x), context=context) + x
        x = self.ff(self.norm3(x)) + x
        return x



class TrasformerBlock(nn.Module):
    """
    Trasformer Block for Unet.
    :in_channels: Number of input tensor channels 
    :num_heads: Number of Attention Head 
    :head_dim: Dimension of Attention Head
    :context_dim: Dimension of context
    :depth: Number of Transformer block Depth (default=1)
    :dropout: DropOut Rate (default=0.)
    :use_spatial: Whether to use Spatial Trasformer Block (defulat=False)
    """
    def __init__(self, 
                 in_channels: int, 
                 num_heads: int = 4, 
                 head_dim: int = 32, 
                 context_dim: int = None,
                 depth: int = 1, 
                 norm_num_groups: int = 8,
                 dropout: float = 0., 
                 use_linear: bool = False,
                 ):
        super().__init__()
        if num_heads == None and head_dim == None:
            raise ValueError("num heads or head_dim should have a value")
        
        elif head_dim == None:
            head_dim = in_channels // num_heads

        elif num_heads is None:
            num_heads = in_channels // head_dim

        inner_dim = num_heads * head_dim

        self.norm = nn.GroupNorm(num_groups=norm_num_groups, num_channels=in_channels, eps=1e-6, affine=True)
        self.use_linear = use_linear
        if not use_linear:
            self.proj_in = nn.Conv2d(in_channels, inner_dim, kernel_size=1, stride=1, padding=0)
            self.proj_out = zero_module(nn.Conv2d(inner_dim, in_channels, kernel_size=1, stride=1, padding=0))
        else:
            self.proj_in = nn.Linear(in_channels, inner_dim)
            self.proj_out = zero_module(nn.Linear(in_channels, inner_dim))

        self.transformer_block = nn.ModuleList([])
        # if use_spatial: # not use currently 
        #     self.trasformer_block.append(SpatialTrasformerBlock(in_channels=in_channels, num_head=num_heads, head_dim=head_dim, context_dim=context_dim, depth=depth, dropout=dropout))
        # else:
        for _ in range(depth):
            self.transformer_block.append(BasicTransformerBlock(dim=in_channels, num_heads=num_heads, head_dim=head_dim, context_dim=context_dim, dropout=dropout))

    def forward(self, x, context=None):
        b, c, h, w = x.shape
        x_in = x
        x = self.norm(x)

        if not self.use_linear: # projection_in if not use linear
            x = self.proj_in(x)

        x = x.permute(0, 2, 3, 1).view(b, -1, c) # b c h w -> b (h w) c

        if self.use_linear: # if use linear
            x = self.proj_in(x)

        for block in self.transformer_block:
            x = block(x, context=context)

        if self.use_linear:
            x = self.proj_out(x)
        
        x = x.view(b, h, w, c).permute(0, 3, 1, 2) # b (h w) c -> b c h w

        if not self.use_linear:
            x = self.proj_out(x)

        x = x + x_in 
        return x
